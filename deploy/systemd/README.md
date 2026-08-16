# Porthouse systemd units

These units run the PostgreSQL migrator once, then keep the public API, Worker and
Scheduler as independently restartable Runtime roles. They assume a wheel/release
is unpacked at `/opt/porthouse/current`, a virtual environment is at
`/opt/porthouse/venv`, and secrets are stored only in `/etc/porthouse.env`.

Before enabling the units, create the private writable Runtime directory:

```bash
install -d -o porthouseruntime -g porthouseruntime -m 0750 /var/lib/porthouse
install -m 0600 -o root -g porthouseruntime /dev/null /etc/porthouse.env
```

Copy `deploy/config.runtime.json` to `/etc/porthouse/config.json`, configure
production-only values in `/etc/porthouse.env`, then install the units and run:

```bash
systemctl daemon-reload
systemctl restart porthouse-migrate.service
systemctl enable --now porthouse-api.service porthouse-worker.service \
  porthouse-scheduler.service porthouse-model-gateway.service
```

`porthouse-migrate.service` uses `RemainAfterExit=yes` so dependent long-running
services retain a successful migration prerequisite. On every new release, restart
the migrator explicitly before restarting API/Worker/Scheduler. Do not put provider,
database, SMTP, or control-plane secrets in the unit files or `config.json`.

`porthouse-model-gateway.service` listens on loopback only. Expose it to a local
Device Host through the Desktop-managed loopback boundary, or place an authenticated
internal TLS proxy in front of it; never expose port 18794 directly to the Internet.
