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
- Robinhood's MCP is built for interactive agent platforms (Claude
  Desktop, Claude Code, ChatGPT, etc.) with a human authenticating via
  browser. Whether that connection/session survives unattended for a
  headless server process running 24/7 is unverified - this is the
  first thing to test once Agentic access is granted, before flipping
  the live switch. Budget accordingly: **Phase 1 (paper trading) does
  not depend on Robinhood access at all.**
- Because of the above, the system is built broker-agnostic from day
  one: a `BrokerAdapter` interface with a `PaperBroker` (real quotes,
  simulated fills/ledger) and a `RobinhoodBroker` stub to fill in once
  access + headless-session behavior are confirmed.

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
                 Broker Adapter (active one) -- PaperBroker (now) or
                        |                        RobinhoodBroker (later)
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

Secrets (`ANTHROPIC_API_KEY`, later Robinhood MCP auth) live in a
git-ignored `.env`, never committed.

## Phasing

1. **Phase 1 - Paper trading (this build)**: PaperBroker + yfinance
   market data + Claude decisions + risk manager + dashboard. Runs
   fully once deployed, no Robinhood dependency.
2. **Phase 2 - Robinhood live**: once Agentic Trading access is
   granted, implement `RobinhoodBroker` against the MCP tools, verify
   headless auth persists, run it side-by-side in paper mode against
   real Robinhood data for a trial period, then flip `bot_state.mode`
   to `live` deliberately (never automatically).
