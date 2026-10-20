---
name: veeam-bare-metal-restore
description: "Use when rebuilding a PC from Veeam recovery media."
---

# Veeam Agent bare-metal restore (Dell laptops)

Recovery media = WinRE boot tool, not an OS image. Booting it should land on the green menu (Bare Metal Recovery / WinRE / Tools); "Windows never loads" from it is expected. A .ppkg cannot be extracted from it.

## Boot pitfalls (check in this order)
1. **Disk on USB-C/Thunderbolt port:** Dell ships Thunderbolt Boot Support + TBT pre-boot modules OFF -> device invisible in F12. Enable both or use USB-A. Turn back off after (DMA risk).
2. **Rufus-written media on NTFS** adds a tiny UEFI:NTFS FAT partition (third-party-CA signed). Needs Dell "Enable Microsoft UEFI CA" = Enabled; may still be rejected by Secure Boot revocations. Prefer Veeam > Create Recovery Media > Removable storage (FAT32, MS bootloader) or Rufus GPT/UEFI/FAT32.
3. Fastboot = Thorough, USB Boot Support on. Manual entry: Boot Sequence > Add Boot Option > \EFI\Boot\bootx64.efi.
4. Suspend BitLocker (`Suspend-BitLocker -MountPoint C: -RebootCount 3`) before changing Secure Boot settings; have recovery key from Entra/Intune.

## Find the backup and check whose it is
- Backups live at `<backup drive>:\VeeamBackup\Job <COMPUTERNAME>\`: the `.vbk` is the full backup (its file size is the backup size; compressed, so far smaller than used space on C:), the `.vbm` is metadata. The job folder name is the source computer name; compare it with the PC being restored before starting. `VEEAMRE` is the drive's volume label, not a folder.

## Disk mapping
- "Unable to auto match disks" / "Cannot find original disk layout" = target disk differs from backup. **First compare the backup's disk/C: sizes with the target**: mismatch means drive was replaced OR wrong machine's backup (identity clash). Confirm computer name before restoring.
- Internal NVMe not listed -> Intel RST/VMD driver (Tools > Load driver) or temporary AHCI (revert after).
- Manual mapping: target = internal disk, never the external backup disk; restore whole disk; **tick every partition incl. the ~100 MB EFI one**. Veeam may pre-tick only C:, which yields an unbootable restore.
- **Verify the Disk Mapping preview before Next:** the target disk row must show ALL backup partitions (EFI ~100 MB, Recovery, C:). If it shows only C: + a few GB unallocated, right-click the target disk -> Clear disk -> Restore disk from -> backup's disk. Also check any extra internal disk (e.g. second SSD 'Storage E:') isn't being overwritten unintentionally.
- BitLocker: locked target -> Unlock to keep encryption, or OK = wipe + restore unencrypted. Resize/new disk = unencrypted.

## Restored but no "Windows Boot Manager" in F12 / Boot Sequence
Symptom: boot list shows only USB + NIC/HTTPS entries while BIOS > Storage still lists the NVMe. Dell **Add Boot Option** file browser = fast diagnostic: it lists every partition the firmware can read; the OS disk's ESP absent there = nothing to point at yet. Read the `HD(n,GPT,...,sectors)` device path on each entry: an entry named after a data volume (e.g. "Storage") whose sector count matches the second SSD is not the OS disk, so don't add it. Boot the Veeam USB > Tools > Command Prompt > `diskpart` > `list disk` > `select disk N` > `list partition`. Only MSR 15 MB + C: with a few GB free = the C:-only mapping trap. Fix in place, no re-restore:
```
select disk N
create partition efi size=260      (free space is usually there; else select volume C -> shrink desired=300)
format quick fs=fat32 label=System (exactly fat32; fs=32 -> "The file system is incompatible")
assign letter=S
list partition
exit
bcdboot C:\Windows /s S: /f UEFI   (-> "Boot files successfully created.")
wpeutil reboot                     (pull the USB as the screen goes black)
```
If `create partition efi` ran twice during a typo loop, `select partition <unformatted one>` + `delete partition override` before bcdboot. The check must run on the TARGET booted from the media: plugging the backup disk into one's own laptop shows that laptop's disks (the diskpart banner hostname says which machine you are on). First boot shows "Getting devices ready" and may reboot once: normal PnP re-detection. Recovery partition stays missing (`reagentc /info` = Disabled); rebuild later.

Guidance style when Andrew types from phone photos at a BIOS/WinPE prompt: one exact line per step, Enter after each, state the expected output; run vision_analyze on the photo to read the command as typed before diagnosing an error (auto-captions paraphrase).

## After restore
- Unplug the recovery disk before reboot (else it boots back into Veeam). No "Windows Boot Manager" entry: see section above (bcdboot from UEFI-booted WinPE also writes the NVRAM entry; Dell Add Boot Option is only the fallback).
- Extend C: if target disk was larger; TBT boot / UEFI CA back off; domain trust: `Test-ComputerSecureChannel -Repair`.
- **Re-baseline the backup.** Once the machine boots from the hand-built ESP, run a fresh full backup from Veeam Agent before BitLocker: the new restore point matches the drive's actual layout (MSR + C: + System), so future restores auto-match and write every partition. Never re-restore the pre-fix backup onto a replaced disk; it reproduces the C:-only mapping. Root cause to state in write-ups: replaced disk -> auto-match fails (signature/size) -> manual mapping pre-ticks C: only -> volume restored, not disk -> reported as success.
- **WinRE first, then BitLocker.** A C:-only restore leaves no Recovery partition and no `Winre.wim` (`reagentc /info` = Disabled, blank location; `Test-Path C:\Windows\System32\Recovery\Winre.wim` = False). Put it back before touching BitLocker: on another Win11 laptop `reagentc /disable` (drops Winre.wim into `C:\Windows\System32\Recovery`), copy it to USB, `reagentc /enable` there again; on the target copy to `C:\Recovery\WindowsRE\`, `reagentc /setreimage /path C:\Recovery\WindowsRE`, `reagentc /enable`. Alternative source: `install.wim` from a Win11 ISO via `dism /Mount-Image ... /ReadOnly`. Winre.wim is hidden+system (invisible in Explorer; verify with `Get-ChildItem D:\Winre.wim -Force`), and a slow USB stick keeps writing after Copy-Item returns (Task Manager removable disk at 99%): wait for 0% before ejecting or `dir D:` hangs.
- BitLocker comes back fully off (new layout = unencrypted write). Enable from PowerShell, not the Control Panel wizard (the wizard's reboot hardware test fails on a rebuilt boot path): `Enable-BitLocker -MountPoint C: -EncryptionMethod XtsAes256 -UsedSpaceOnly -TpmProtector -SkipHardwareTest`, then `Add-BitLockerKeyProtector -MountPoint C: -RecoveryPasswordProtector`, then `$id = (Get-BitLockerVolume -MountPoint C:).KeyProtector | Where-Object KeyProtectorType -eq 'RecoveryPassword' | Select-Object -ExpandProperty KeyProtectorId` and `BackupToAAD-BitLockerKeyProtector -MountPoint C: -KeyProtectorId $id` (two steps; a one-line pipeline typed from a phone gets mangled and prompts for KeyProtectorId). Intune may re-apply within the hour; check `manage-bde -status C:` before doubling up.
- "BitLocker could not be enabled / encryption key cannot be obtained, verify TPM" or `FileNotFoundException` from `Add-TpmProtectorInternal` after a restore is usually NOT the TPM. Diagnose in this order, one command per line: `Get-Tpm | Format-List *` (TpmReady/TpmOwned True, ManagedAuthLevel Full = healthy; clear only if not), `Confirm-SecureBootUEFI`, `Get-ItemProperty HKLM:\SOFTWARE\Policies\Microsoft\FVE` (blank = no Intune/GPO pin), `reagentc /info`. BitLocker events 893/813/881 (TCG log / Secure Boot / boot-manager signature) are PCR7 fallback warnings, not the blocker. Healthy TPM + Secure Boot on + no policy + WinRE missing -> restore WinRE (above) and retry; if it still fails, refresh boot files from inside Windows (`mountvol S: /s`, `bcdboot C:\Windows /s S: /f UEFI`, `mountvol S: /d`) so the boot manager signature matches the firmware db.

## Giving PowerShell/diskpart instructions over chat
- One command per message block, no trailing `# comments`: PSReadLine pastes a multi-line block as one buffer and a `#` on the first line comments out every line after it, so only the first command runs (you'll see `>>` continuation prompts and a single "Operation Successful"). A lone `>>` prompt means an unfinished line: Ctrl+C and retype.
- Quote braces for native commands: `bcdedit /enum '{bootmgr}'` (PowerShell parses bare `{}` as a script block).
- Never paste a recovery password back into chat; treat one shown on a photo as exposed and rotate after the TPM protector is in place.
