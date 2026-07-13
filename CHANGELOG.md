# Changelog

Entries here are limited to changes that affect **trading behavior**,
or who/what can reach the controls that affect it: risk limits,
strategy/prompt changes, broker logic, access-control changes to the
dashboard/API, or anything else that changes what the bot actually
does. Not a full commit log or feature list — see `git log` for
everything else.

## 2026-07-13 — Raised max_tokens to stop responses truncating mid-JSON

- `app/strategy/claude_decision_engine.py`: `max_tokens` raised from
  `2000` to `6000`.

**Why:** once scan mode raised the per-tick ticker count above the
original fixed 5, ticks started failing with
`Could not parse Claude response as JSON` (logged, decisions silently
dropped). Root cause: the model's response includes a large internal
reasoning block that can consume over half of `max_tokens` on its own,
so the actual JSON answer got cut off mid-object once there wasn't
enough budget left for ~12+ tickers'-worth of output
(`stop_reason: "max_tokens"`, confirmed by direct reproduction).

## 2026-07-13 — Claude decision prompt made less conservative

- `app/strategy/claude_decision_engine.py`: `SYSTEM_PROMPT`'s rules no
  longer tell Claude "most ticks should result in mostly hold decisions
  - only act when there is a clear signal." Now instructs it to act on
  moderate signals (RSI extremes, a clear SMA20/SMA50 break, a sharp
  move with supporting momentum) and treat "hold" as the exception
  rather than the default.

**Why:** paired with the scan-mode change above — during testing, every
tick was returning "hold" across the board even on tickers with
meaningful moves, because the prompt explicitly biased toward inaction.
Position sizing and risk limits are still fully enforced downstream by
`RiskManager` regardless of this change; it only affects how readily
Claude proposes a buy/sell in the first place.

## 2026-07-13 — Watchlist switched from fixed to scan mode

- `app/watchlist.yaml`: `mode: fixed` → `mode: scan`. On top of the 5
  fixed tickers (AAPL/MSFT/NVDA/AMD/TSLA), each tick now adds up to
  `max_scan_candidates` (10) of the day's biggest movers (by absolute
  day-change %) from a 16-name universe, via
  `YFinanceAdapter.scan_movers()` — already implemented, previously
  unused since `mode` had never been switched on.

**Why:** once paper trading was actually producing decisions (previous
entry), the fixed 5-ticker watchlist wasn't showing enough volatility to
produce anything but "hold". Scanning for real movers gives the
decision engine more candidates that are more likely to have an
actionable technical setup.

## 2026-07-13 — Fixed dependency breakage that silently prevented paper trading

- `requirements.txt`: `httpx` pinned back to `0.27.2` (had been bumped to
  `0.28.1` for the Phase 2 `mcp` dependency). httpx 0.28 dropped a
  `proxies` kwarg that `anthropic==0.34.2` still passes internally, so
  every tick's Claude call crashed with `TypeError` before producing a
  decision. `mcp==1.28.1` only requires `httpx<1.0.0,>=0.27.1`, so
  `0.27.2` satisfies both.
- `yfinance` bumped to `0.2.66` (was `0.2.43`) — Yahoo's backend changed
  in a way the old release couldn't handle, so every ticker request came
  back as an empty/non-JSON response ("possibly delisted"), even for
  obviously-live tickers like AAPL.

**Why:** the dashboard and scheduler logs both showed the bot as
"running" the whole time — `run_tick`'s own try/except logs and
swallows per-tick failures rather than crashing the process — but it
was silently producing zero real decisions. Both bugs were introduced
incidentally alongside the Phase 2 MCP dependency additions, not by any
`TRADING_MODE`/`LIVE_DRY_RUN` change; paper mode itself was just broken.

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

## 2026-07-12 — RobinhoodBroker implemented (Phase 2), dry-run only

- `app/brokers/robinhood_broker.py`: replaces the `NotImplementedError`
  stub with a real implementation against Robinhood's Trading MCP
  (`get_accounts`, `get_portfolio`, `get_equity_positions`,
  `review_equity_order`, `place_equity_order` - tool *names* confirmed
  against Robinhood's own support docs; exact input/output field names
  are our best guess, called out explicitly in that file's mapping
  comment, and need checking against real
  `scripts/robinhood_list_tools.py` output before this is trusted).
- `app/brokers/robinhood_oauth.py` (new): OAuth 2.1 + PKCE + dynamic
  client registration via the official `mcp` Python SDK, the same
  generic mechanism `claude mcp add --transport http` uses. Tokens
  persist to `ROBINHOOD_TOKEN_FILE` (new setting, default
  `secrets/robinhood_tokens.json`, git-ignored, bind-mounted so it
  survives rebuilds). This module never attempts an interactive login
  itself - `scripts/robinhood_oauth_setup.py` (new) is the one-time
  human-in-the-browser step; everything else (the broker, in
  production) is headless and raises `RobinhoodAuthError` instead of
  blocking on a login prompt that isn't there.
- `app/scheduler.py`: catches `RobinhoodAuthError` around the
  broker-dependent part of each tick and auto-pauses the bot
  (`bot_state.running = False`, reason set to the error) exactly like
  the risk manager's kill switch does - never retries silently, never
  flips `TRADING_MODE`.
- New `LIVE_DRY_RUN` setting (default **true**). `RobinhoodBroker.place_order`
  always calls `review_equity_order` (Robinhood's own pre-trade
  simulation, documented as never executing) but skips
  `place_equity_order` (the real fill) while this is true - so
  decisions/reasoning still populate the dashboard as
  `[DRY RUN] ...` non-executed entries, letting live-mode behavior be
  compared against paper before any real order is ever placed.
- `RobinhoodBroker` refuses to resolve an account (and thus refuses to
  trade) if `get_accounts` returns more than one account - it will not
  guess which one is the isolated Agentic account.
- New `scripts/robinhood_list_tools.py`: dumps every tool's
  name/description/input schema/output schema via the same headless
  connection path the broker uses, to check against the field names
  hardcoded in `robinhood_broker.py`.

**Why:** this is Phase 2 from `day-trader-spec.md`, now implemented
but deliberately not yet trusted with money - two independent gates
(`TRADING_MODE` still `paper`, and `LIVE_DRY_RUN=true` even once it
isn't) stand between this code and a real order. `LIVE_DRY_RUN` stays
true regardless of what `TRADING_MODE` gets set to until explicitly
told otherwise.

**Known gaps, not yet resolved:**
- Field-name assumptions in `robinhood_broker.py` (cash/equity/position
  fields, `review_equity_order`/`place_equity_order` argument names)
  are unverified against a real Robinhood response - pending running
  `scripts/robinhood_oauth_setup.py` + `scripts/robinhood_list_tools.py`
  interactively (requires a human browser login, can't be automated).
- Whether headless OAuth refresh actually survives unattended for
  days/weeks, as opposed to just being spec-compliant in principle, is
  unverified until it's been running that way in practice.
