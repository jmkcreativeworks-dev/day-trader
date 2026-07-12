# Changelog

Entries here are limited to changes that affect **trading behavior**:
risk limits, strategy/prompt changes, broker logic, or anything else
that changes what the bot actually does. Not a full commit log or
feature list — see `git log` for everything else.

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
