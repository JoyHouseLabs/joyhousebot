# JoyhouseBot systemd units

These units run the PostgreSQL migrator once, then keep the public API, Worker and
Scheduler as independently restartable Runtime roles. They assume a wheel/release
is unpacked at `/opt/joyhousebot/current`, a virtual environment is at
`/opt/joyhousebot/venv`, and secrets are stored only in `/etc/joyhousebot.env`.

Before enabling the units, create the private writable Runtime directory:

```bash
install -d -o joyhouseruntime -g joyhouseruntime -m 0750 /var/lib/joyhousebot
install -m 0600 -o root -g joyhouseruntime /dev/null /etc/joyhousebot.env
```

Copy `deploy/config.runtime.json` to `/etc/joyhousebot/config.json`, configure
production-only values in `/etc/joyhousebot.env`, then install the units and run:

```bash
systemctl daemon-reload
systemctl restart joyhousebot-migrate.service
systemctl enable --now joyhousebot-api.service joyhousebot-worker.service \
  joyhousebot-scheduler.service joyhousebot-model-gateway.service
```

`joyhousebot-migrate.service` uses `RemainAfterExit=yes` so dependent long-running
services retain a successful migration prerequisite. On every new release, restart
the migrator explicitly before restarting API/Worker/Scheduler. Do not put provider,
database, SMTP, or control-plane secrets in the unit files or `config.json`.

`joyhousebot-model-gateway.service` listens on loopback only. Expose it to a local
Device Host through the Desktop-managed loopback boundary, or place an authenticated
internal TLS proxy in front of it; never expose port 18794 directly to the Internet.
