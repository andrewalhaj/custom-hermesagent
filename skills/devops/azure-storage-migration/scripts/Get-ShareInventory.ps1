# Get-ShareInventory.ps1 - read-only inventory of SMB shares on this server.
# Run on the file server (RDP), elevated:  powershell -ExecutionPolicy Bypass -File .\Get-ShareInventory.ps1
# Output: .\share-inventory-<host>-<date>.json (aggregates only; no file names unless -IncludePaths)
param(
    [string[]]$Share,          # share names to scan; default = all non-admin shares
    [switch]$IncludePaths,     # add the 10 largest files' paths (review before sending)
    [int]$TopExt = 20
)
$ErrorActionPreference = 'SilentlyContinue'
$now = Get-Date
$shares = Get-SmbShare | Where-Object { $_.Path -and -not $_.Special -and $_.Name -notmatch '\$$' }
if ($Share) { $shares = $shares | Where-Object { $Share -contains $_.Name } }
$out = [ordered]@{ host = $env:COMPUTERNAME; os = (Get-CimInstance Win32_OperatingSystem).Caption; scanned = $now.ToString('s'); shares = @() }

foreach ($s in $shares) {
    Write-Host "Scanning $($s.Name) ($($s.Path)) ..."
    $root = '\\?\' + $s.Path.TrimEnd('\')
    $files = Get-ChildItem -LiteralPath $root -Recurse -Force -File -ErrorVariable errs
    $denied = @($errs | Where-Object { $_.Exception -is [System.UnauthorizedAccessException] }).Count
    $n = @($files).Count
    $bytes = ($files | Measure-Object Length -Sum).Sum
    $sizes = @{ lt1MB = 0; '1to16MB' = 0; '16to100MB' = 0; gt100MB = 0 }
    $age = @{ mod30d = 0; mod90d = 0; mod1y = 0; untouched5y = 0 }
    $maxDepth = 0; $longest = 0; $over260 = 0; $hidden = 0; $reparse = 0
    $ext = @{}
    $baseDepth = ($s.Path.TrimEnd('\') -split '\\').Count
    foreach ($f in $files) {
        $L = $f.Length
        if ($L -lt 1MB) { $sizes.lt1MB++ } elseif ($L -lt 16MB) { $sizes.'1to16MB'++ } elseif ($L -lt 100MB) { $sizes.'16to100MB'++ } else { $sizes.gt100MB++ }
        $d = ($now - $f.LastWriteTime).Days
        if ($d -le 30) { $age.mod30d++ }; if ($d -le 90) { $age.mod90d++ }; if ($d -le 365) { $age.mod1y++ }; if ($d -gt 1826) { $age.untouched5y++ }
        $p = $f.FullName -replace '^\\\\\?\\', ''
        $len = $p.Length; if ($len -gt $longest) { $longest = $len }; if ($len -gt 260) { $over260++ }
        $depth = ($p -split '\\').Count - $baseDepth; if ($depth -gt $maxDepth) { $maxDepth = $depth }
        if ($f.Attributes -band [IO.FileAttributes]::Hidden) { $hidden++ }
        if ($f.Attributes -band [IO.FileAttributes]::ReparsePoint) { $reparse++ }
        $e = if ($f.Extension) { $f.Extension.ToLower() } else { '(none)' }
        $ext[$e] = 1 + $ext[$e]
    }
    $top = $files | Sort-Object Length -Descending | Select-Object -First 10
    $acl = (Get-Acl -LiteralPath $s.Path).Access | ForEach-Object { "$($_.IdentityReference): $($_.FileSystemRights)" } | Sort-Object -Unique
    $out.shares += [ordered]@{
        name = $s.Name; path = $s.Path
        share_permissions = @(Get-SmbShareAccess -Name $s.Name | ForEach-Object { "$($_.AccountName): $($_.AccessRight)" })
        ntfs_root_acl = @($acl)
        open_to_everyone = [bool]($acl -match '^(Everyone|BUILTIN\\Users|.*\\Domain Users|Authenticated Users)')
        files = $n; total_gb = [math]::Round($bytes / 1GB, 2)
        size_buckets = $sizes; age = $age
        max_depth = $maxDepth; longest_path_chars = $longest; paths_over_260 = $over260
        hidden = $hidden; reparse_points = $reparse; access_denied_dirs = $denied
        top_extensions = @($ext.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $TopExt | ForEach-Object { @{ ext = $_.Key; count = $_.Value } })
        largest_10 = @($top | ForEach-Object { @{ mb = [math]::Round($_.Length / 1MB, 1); ext = $_.Extension; path = $(if ($IncludePaths) { $_.FullName -replace '^\\\\\?\\', '' } else { $null }) } })
    }
}
$file = ".\share-inventory-$($env:COMPUTERNAME)-$($now.ToString('yyyyMMdd')).json"
$out | ConvertTo-Json -Depth 6 | Set-Content -Path $file -Encoding UTF8
Write-Host "Done -> $file"
