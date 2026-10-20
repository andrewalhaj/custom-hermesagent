# Swap on Memory-Constrained Hosts (Backup VPS Pattern)

## When to add swap vs. upgrade RAM

**The decision framework (from the <vps-ip> backup host, 2026-06-06):**
(real address: memory_search "Tailnet")

| Signal | Swap | RAM upgrade |
|--------|------|-------------|
| Steady-state available >500MB, no swap active | **Add swap** | Not needed |
| Available <300MB persistently | Swap is the buffer, upgrade when swapping is regular | **Consider upgrade** |
| Load <1.0, no OOM events | Swap for spike protection only | Not needed |
| Swap used routinely, load >2.0 | Overdue — upgrade ASAP | **Upgrade** |

The backup host (1.9Gi RAM, 800Mi available, load 0.5, no swap) was at risk
from silent OOM-kills on memory spikes (HA updates, automation bursts). Adding
2GB swap for zero cost converted the failure mode from "fatal, silent" to
"degraded, visible." The host was NOT pressured in steady-state — it lacked a
buffer, not capacity.

## The procedure

```bash
# Read-only probe first (no changes)
ssh -o BatchMode=yes root@<host> '
  free -h; swapon --show; df -h /; cat /proc/loadavg
  ps -eo pid,comm,%mem,rss --sort=-rss | head -6   # top consumers
'

# Add 2GB swapfile (22G free disk minimum)
ssh root@<host> '
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  sysctl vm.swappiness=10              # favor RAM, swap only under real pressure
  echo "/swapfile none swap sw 0 0" >> /etc/fstab   # persistent
  free -h; swapon --show              # verify
'
```

## Key design choices

- **swappiness=10**, not 60 (default). The box should use RAM aggressively and
  only touch swap under real pressure. This is a safety net, not active tiering.
- **fstab entry** for reboot persistence. Without it, maintainers forget swap
  was added and the box silently returns to unbuffered state.
- **Identify the top RAM consumer FIRST** (the `ps` line). If it's a leak that
  grows unbounded, swap just delays the OOM — fix the leak, then add swap.

## Rollback

```bash
swapoff /swapfile; rm /swapfile; sed -i '/swapfile/d' /etc/fstab
# Fully state-reversible, zero cost, no config drift.
```

## Pitfall — swap hides a memory leak

If a process is growing unbounded (confirmed by tracking RSS over time),
adding swap defers the problem rather than fixing it. A box that swaps
routinely under normal load needs more RAM, not more swap. Use swap for
spike protection; upgrade RAM for steady-state pressure.
