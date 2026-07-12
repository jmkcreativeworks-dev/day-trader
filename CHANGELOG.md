# Changelog

Entries here are limited to changes that affect **trading behavior**,
or who/what can reach the controls that affect it: risk limits,
strategy/prompt changes, broker logic, access-control changes to the
dashboard/API, or anything else that changes what the bot actually
does. Not a full commit log or feature list — see `git log` for
everything else.

## 2026-07-11 — Paper account balance is now dashboard-editable/resettable

- `bot_state.paper_starting_cash` (new column, self-migrating) replaces
  the `PAPER_STARTING_CASH` env var as the source of truth for
  `PaperBroker`'s starting cash.
- New `POST /actions/reset-paper`: wipes all `mode='paper'` rows from
  `trades`, `positions`, `portfolio_snapshots`, and `ai_decisions`,
  then sets the new baseline balance — from a dashboard form with a
  confirm dialog (destructive).

**Why:** the balance could previously only change via `.env` + restart,
which meant the on-disk env var and the actual paper ledger could
silently drift apart. Making the reset a deliberate, logged dashboard
action means the starting balance for any given equity curve is always
whatever the "Paper account" card says, not whatever `.env` happened to
contain at container-start time.

## 2026-07-12 — HTTP Basic Auth added to the whole app

- New `app/auth.py` (`BasicAuthMiddleware`, raw ASGI middleware) gates
  every route except `/healthz` behind `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD`, checked with `secrets.compare_digest`.
- App now refuses to start (`RuntimeError` at import time) if
  `DASHBOARD_PASSWORD` is blank.

**Why:** the dashboard's pause/resume, risk-limit, and paper-reset
controls were reachable by anyone who could hit the box's LAN or
Tailscale IP directly on port 3002, bypassing the Cloudflare Access
gate in front of the normal hostname. Basic Auth is defense in depth
for that path, not the primary access control.
