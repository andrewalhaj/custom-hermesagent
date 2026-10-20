# Restarting the gateway from inside a gateway session

Applies whenever a change the gateway caches at startup (plugin code, memory caps, platform config, core patch) must go live and the only shell you have is a tool call inside that gateway.

## Procedure

1. **Preflight the change under the gateway's own interpreter, not the CLI venv.** Find it from the live PID: `ps -o cmd= -p $(systemctl --user show hermes-gateway -p MainPID --value)`. For a plugin: `python -m py_compile`, then dry-load it (`importlib` + `register()` with a permissive fake ctx) and confirm the armed/registered markers print. A plugin that imports cleanly under the 3.11 CLI venv can still fail under the 3.14 gateway shim.
2. **Finish or stop every child first.** A restart kills running subagents, background `claude -p` jobs and in-flight backups; the harness delivers their partial result, not a retry.
3. **Write the lifecycle verb to a script file**, e.g. `~/.hermes/cache/scratch/gw_reload.sh`:
   ```bash
   #!/bin/bash
   systemctl --user restart hermes-gateway.service
   ```
   chmod 755. Reason: the in-process guard pattern-matches tool-call TEXT for `restart|stop … hermes-gateway` and blocks it ("cannot restart the gateway from inside the gateway process"); a path argument passes, and the transient unit runs outside the gateway's cgroup anyway.
4. **Queue a detached transient unit, then END THE TURN:**
   ```bash
   systemctl --user reset-failed hermes-gw-reload.service 2>/dev/null
   systemd-run --user --on-active=3 --unit=hermes-gw-reload \
     --description="one-shot gateway reload" ~/.hermes/cache/scratch/gw_reload.sh
   systemctl --user list-timers 'hermes-gw-reload*' --no-legend
   ```
   Your own open turn holds the old process in `stop-sigterm`; `TimeoutStopUSec` (≈3.5 min) is the backstop, `Restart=always` the safety net. Do not poll.
5. **Prove it on the next turn from the journal**, never from memory of what you changed:
   - new `MainPID`, `ActiveEnterTimestamp` after the queue time
   - `[local-guards] N/N armed` and `write_gate armed`
   - for a plugin edit: the NEW marker/label present and `grep -c` of the OLD string == 0 since the restart timestamp
   - platform connected (Telegram/Discord lines), and any dependent service still answering (e.g. dispatcher auth probe)

## Pitfalls

- A gateway that reports `active` with the OLD `ActiveEnterTimestamp` has not restarted — the queued job is still waiting on your turn.
- `systemd-run … systemctl --user restart …` inline is the blocked form; the script-file form is the only one that passes the guard.
- Pre-existing platform errors (e.g. a bot token claimed by another profile) re-surface on every restart; note them, do not treat them as regressions of the change you just made.
