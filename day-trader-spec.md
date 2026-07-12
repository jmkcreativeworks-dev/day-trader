# day-trader — AI Day Trading + Monitoring System

Spec for a Claude-driven paper/live day-trading bot with a web dashboard,
built to slot into the shared JMK Equipment Apps dev conventions (own
Docker Compose stack under `~/src/day-trader`, own ports, isolated from
`jmk-app`/`jmk-db`/`jmk-cloudflared`).

## Goals

1. Claude decides trades on a schedule during market hours, based on a
   watchlist/scan of tickers, recent quotes, and technical indicators.
2. Every decision runs in **paper mode** first - real market data, fake
   money, full logging - until the account behavior is trusted.
3. A **risk manager** enforces hard limits (position size, daily loss,
   open positions) independent of what Claude decides, and can kill
   trading automatically.
4. A **web dashboard** shows portfolio value over time, open positions,
   every trade with Claude's stated reasoning, paper/live mode + pause
   controls, and a paper-account reset control (change the starting
   balance and wipe paper history without a restart).
5. **Live mode** later routes the same decision pipeline through
   Robinhood's official Agentic Trading MCP instead of the paper broker
   - no other code changes needed.

## Important constraints discovered during research

- Robinhood has no traditional REST trading API for stocks. It has a
  new (2026, beta) **Agentic Trading** product: a dedicated brokerage
  sub-account plus an MCP server (`https://agent.robinhood.com/mcp/trading`)
  that gives an AI agent tools to check balances/positions, pull quotes
  and indicators, and place/cancel equity + options orders. Access
  rolls out by invitation - request it in the Robinhood app/site.
- Robinhood's MCP has **no paper-trading mode** - it's real order
  placement in a real (if budget-limited) account. It does have a
  `review_equity_order` tool for pre-trade simulation/warnings, but
  that's a one-off check, not an ongoing sandbox.
- Robinhood's MCP uses the standard MCP Authorization spec (OAuth 2.1,
  PKCE, dynamic client registration) - the same generic mechanism
  `claude mcp add --transport http <url>` uses, confirmed against
  Robinhood's own docs ("paste one URL into your MCP config to connect
  most agents out of the box"). That means a standard client (we use
  the official `mcp` Python SDK's OAuth support, see
  `app/brokers/robinhood_oauth.py`) can request offline access
  (`refresh_token` grant) and refresh without a human present -
  *in principle*. Whether that actually holds up unattended for days/
  weeks on a headless server is still **operationally unverified**;
  `app/scheduler.py` auto-pauses the bot (never retries silently) if a
  session ever needs re-authentication, rather than assuming it works.
- Because of the above, the system is built broker-agnostic: a
  `BrokerAdapter` interface with `PaperBroker` (real quotes, simulated
  fills/ledger) and `RobinhoodBroker` (real, implemented Phase 2 - see
  `CHANGELOG.md` for the date, and the mapping comment at the top of
  `app/brokers/robinhood_broker.py` for exactly which tool-name/field
  assumptions are confirmed vs. still best-guess).

## Architecture

```
                 Scheduler (loop) -- every N minutes, market hours only
                        |
                 Market Data Adapter -- quotes + indicators for watchlist
                        |
                 Claude Decision Engine -- Anthropic API call: portfolio +
                        |                  market data in, structured
                        |                  {action, symbol, qty, reasoning}
                        |                  decisions out
                 Risk Manager -- clamps/blocks decisions that violate
                        |         position size / daily loss / max-positions
                 Broker Adapter (active one, by TRADING_MODE) --
                        |    PaperBroker, or RobinhoodBroker (real MCP
                        |    calls, gated by LIVE_DRY_RUN - see below)
                 Postgres -- decisions, trades, positions, snapshots, config
                        |
                 FastAPI dashboard -- equity curve, positions, trade log
                                       w/ reasoning, mode + pause
```

## Data model (Postgres)

- `bot_state` - single row: mode (paper/live), running/paused, last
  tick time, kill-switch reason if paused automatically, paper account
  starting balance (`paper_starting_cash`, dashboard-editable, self-
  migrated column - see `CHANGELOG.md`).
- `risk_config` - max position size %, max daily loss %, max open
  positions, per-trade dollar cap, editable from the dashboard.
- `portfolio_snapshots` - timestamp, cash, positions value, total
  value, mode. One row per scheduler tick -> drives the equity curve.
- `positions` - symbol, quantity, average cost, mode.
- `trades` - timestamp, symbol, side, quantity, price, mode, linked
  decision_id.
- `ai_decisions` - timestamp, symbol, action, quantity/dollar amount,
  confidence, full reasoning text, whether risk manager modified/
  blocked it and why, raw model response for audit.
- `dashboard_users` - username (unique), bcrypt password_hash,
  created_at. Backs the dashboard's HTTP Basic Auth (see "Access
  control" below); managed from the `/users` page, no restart needed.

## Access control

The whole app sits behind HTTP Basic Auth (`app/auth.py`,
`BasicAuthMiddleware`) as defense in depth, in case it's ever reached
directly via LAN/Tailscale IP instead of through the Cloudflare-Access-
gated hostname (`/healthz` is exempt, for container health checks).
Credentials are DB-backed (`dashboard_users`, bcrypt via
`bcrypt.checkpw`) so accounts can be added/removed from `/users`
without a restart. `DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` in `.env`
only bootstrap the first account when that table is empty - the app
refuses to start if it's empty and the env password is blank, and
ignores those two vars entirely once any user exists.

Any logged-in user can currently add or delete any other user,
including deleting someone else's account (the only protections are:
can't delete yourself, can't delete the last remaining user). Fine
while it's a single-person tool; worth revisiting if a second person
ever gets an account.

## Watchlist / universe

Config-driven (`watchlist.yaml`), supports either:
- a fixed list of tickers you specify, or
- a "scan" mode (most active / biggest movers) pulled from the market
  data adapter, capped to a max number of candidates per tick to keep
  API/token usage bounded.

Both stock and (later) options instruments are supported by the data
model and broker interface; options trading is off by default in
`risk_config` (`allow_options: false`) until you explicitly turn it on.

## Risk manager rules (defaults - all editable in the dashboard/.env)

- Max 5% of portfolio value per open position
- Max 2% total daily loss before the bot auto-pauses for the rest of
  the day and flags itself on the dashboard
- Max 8 open positions at once
- Options trading disabled by default
- Manual pause/resume always available regardless of the above

## Deployment shape

Own Docker Compose stack under `~/src/day-trader`, isolated Postgres
(different container name/volume/port from `jmk-db`), app container
exposing the dashboard on its own host port. Exact host ports TBD -
check `~/src/README.md` for the next free ports before first deploy
(placeholders used in this repo: **3002** app, **5434** db - confirm
and change if already taken).

Secrets (`ANTHROPIC_API_KEY`, dashboard bootstrap credentials) live in
a git-ignored `.env`, never committed. Robinhood OAuth tokens (from
`scripts/robinhood_oauth_setup.py`) live in a git-ignored `secrets/`
directory, bind-mounted into the container (`ROBINHOOD_TOKEN_FILE`) so
they survive rebuilds without being baked into the image or committed.
The container also publishes port **3030**, used only transiently by
`robinhood_oauth_setup.py`'s local OAuth callback listener.

## Phasing

1. **Phase 1 - Paper trading**: PaperBroker + yfinance market data +
   Claude decisions + risk manager + dashboard. Runs fully once
   deployed, no Robinhood dependency. Done.
2. **Phase 2 - Robinhood live**: `RobinhoodBroker` is implemented
   against the MCP tools (`app/brokers/robinhood_broker.py`,
   `app/brokers/robinhood_oauth.py`), gated by two independent safety
   layers before it can move real money:
   - `TRADING_MODE` must be `live` (still `paper` as of this writing -
     never flipped automatically, see rule below).
   - Even then, `LIVE_DRY_RUN` (default **true**) makes `place_order`
     stop after Robinhood's own `review_equity_order` simulation -
     `place_equity_order` (the real fill) is never called while it's
     true, but decisions/reasoning still populate the dashboard so
     live-mode behavior can be compared against paper before it's
     trusted.
   - Still to do before ever setting `LIVE_DRY_RUN=false`: confirm via
     `scripts/robinhood_list_tools.py` that the field-name assumptions
     in `robinhood_broker.py` actually match Robinhood's responses;
     verify headless re-auth actually survives days/weeks unattended
     (not just that the code *should* support it); run it side-by-side
     against paper mode for a trial period.
   - Never flip `TRADING_MODE` or `LIVE_DRY_RUN` automatically - always
     a deliberate `.env` change + restart.
