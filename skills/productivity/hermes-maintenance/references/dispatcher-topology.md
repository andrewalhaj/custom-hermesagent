# Dispatcher Service Topology

## The trap

The dispatcher code lives at `~/hermes-dispatcher/` but there is **NO `hermes-dispatcher` service.** It runs as an ExecStart override on `hermes-dashboard.service`.

The filesystem path and the service name share no naming convention — pattern-matching the directory name to a service name is the #1 failure mode. Every session that touches the dispatcher tends to invent `systemctl restart hermes-dispatcher` which has never existed.

## Reality

```ini
# /etc/systemd/system/hermes-dashboard.service.d/dispatcher-override.conf
[Service]
ExecStart=
ExecStart=%h/hermes-dispatcher/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8787
WorkingDirectory=%h/hermes-dispatcher
Environment=HERMES_HOME=%h/.hermes
EnvironmentFile=%h/hermes-dispatcher/.env
Restart=always
RestartSec=3
```

The restart command is ALWAYS `systemctl restart hermes-dashboard`.

## Key paths

| What | Path |
|---|---|
| Dispatcher code | `~/hermes-dispatcher/` |
| Webhook handlers | `~/hermes-dispatcher/routes/hooks.py` |
| Environment | `~/hermes-dispatcher/.env` |
| WebUI | `~/hermes-webui/` |
| Dashboard (WebUI frontend) | `~/hermes-dashboard/` |
| Service unit | `hermes-dashboard.service` |
| Service override | `/etc/systemd/system/hermes-dashboard.service.d/dispatcher-override.conf` |
| Port | 8787 |
| Kanban DB | `~/.hermes/kanban.db` |
