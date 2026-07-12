# Working conventions for this project

## Git — every change, right away

This repo is tracked at `git@github.com:jmkcreativeworks-dev/day-trader.git`
(SSH, same account/convention as the other `~/src/*` projects).

- After **any** change — feature, bug fix, config change, dependency
  bump, doc update, anything — commit it and `git push` immediately.
  Don't leave work uncommitted or unpushed at the end of a turn.
- One logical change per commit. Don't batch unrelated changes together
  just because they happened in the same session.
- Commit messages should be clear and specific about *what* changed and
  *why*, not generic ("fix bug", "update files").

## CHANGELOG.md — trading-behavior changes only

Add an entry to `CHANGELOG.md` (project root) whenever a change affects
what the bot actually does, or who/what can reach the controls that
affect it: risk limits, strategy/prompt changes, broker logic, anything
touching order placement or position sizing, and access-control changes
to the dashboard/API (auth, exposed ports, etc.). Not for unrelated
changes (dashboard styling, deploy docs, etc.).

Each entry: date, what changed, why. This is the audit trail for "what
logic was live" if a trade ever looks wrong in hindsight — treat it as
more load-bearing than a normal app's changelog.

## day-trader-spec.md — keep it current, same commit

If a change alters the architecture (data model, broker interface,
deployment shape) or the phasing plan (e.g. Robinhood live-trading
progress), update `day-trader-spec.md` in the **same commit** as the
code change, not as a follow-up later. The spec should always describe
the system as it currently exists.
