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

## 2026-07-12 — Basic Auth replaced with DB-backed multi-user accounts

- New `dashboard_users` table (username, bcrypt `password_hash`,
  `created_at`). `app/auth.py` rewritten: `BasicAuthMiddleware` now
  checks credentials against this table via `bcrypt.checkpw`, runs a
  dummy hash check on unknown usernames (timing-attack mitigation so
  "wrong password" and "no such user" take the same time), and stashes
  the authenticated username on `request.state.username`.
- `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` in `.env` now only seed the
  first account when `dashboard_users` is empty; app still refuses to
  start if it's empty and the env password is blank. Once any user
  exists, those env vars are never read again.
- New `/users` page: list users, add a user (username + password, 8-
  char minimum, confirmed twice), delete a user. Can't delete the
  account you're logged in as or the last remaining user. Linked from
  the dashboard header.

**Why:** single shared credential meant there was no way to revoke one
person's access without changing the password for everyone (previous
entry, above). Per-user accounts managed in-app mean access can be
added/removed without touching `.env` or restarting the container.

**Known gap, not yet addressed:** any logged-in user can currently add
or delete any other user, including ones they didn't create — the only
guardrails are "can't delete yourself" and "can't delete the last
user." Not a problem while this is single-person tooling; would need
tightening (e.g. an admin flag) before giving a second person access.
