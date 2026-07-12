# Deploying day-trader to jmk-web01 (devbox)

## 1. Get the code onto devbox

From your machine (VS Code Remote-SSH into `devbox`, or plain `scp`/`git`):

```bash
# on devbox
mkdir -p ~/src/day-trader
# copy this whole day-trader/ folder into ~/src/day-trader
# (or: push it to a repo under the jmkcreativeworks-dev GitHub account
# and `git clone` it into ~/src/day-trader instead)
```

## 2. Confirm ports are free

Check `~/src/README.md` for the current port allocation table. This
project defaults to:

- **3002** — dashboard (host) → 8000 (container)
- **5434** — Postgres (host) → 5432 (container)

If either is taken, edit the `ports:` lines in `docker-compose.yml` and
add a row for `day-trader` to `~/src/README.md`.

## 3. Configure secrets

```bash
cd ~/src/day-trader
cp .env.example .env
nano .env
```

You need:
- `ANTHROPIC_API_KEY` — a real API key from console.anthropic.com. This
  is **separate** from the Claude Pro login used by the Claude Code CLI
  on this box — that login doesn't grant programmatic Messages API
  access, so a dedicated API key is required here.
- Everything else can stay at its default for the first run
  (paper mode, $10,000 virtual cash, conservative risk limits).

## 4. Edit the watchlist (optional)

`app/watchlist.yaml` is mounted read-only into the container, so you
can change tickers or switch `mode: fixed` → `mode: scan` without a
rebuild — just restart the container after editing
(`docker compose restart day-trader-app`).

## 5. Build and run

```bash
docker compose up -d --build
docker compose logs -f day-trader-app
```

Visit `http://<devbox-ip>:3002/` (LAN `192.168.1.107` or Tailscale
`100.102.107.115`) for the dashboard.

## 6. What to expect on first run

- The bot only trades during US market hours (9:30–16:00 ET, weekdays)
  — outside that window it just sits there, which is expected.
- Every tick (default every 15 min) it logs one AI decision per
  watchlist ticker, even "hold" decisions, so the dashboard fills in
  even on quiet days.
- It starts in **paper mode** with $10,000 virtual cash. Nothing here
  touches a real brokerage account until `TRADING_MODE=live` is set
  *and* `app/brokers/robinhood_broker.py` has been implemented (see
  the comments in that file — it currently raises on purpose).

## 7. Going live later (Phase 2)

1. Request Robinhood Agentic Trading access
   (robinhood.com/us/en/agentic-trading) and open the dedicated
   Agentic account.
2. Fund only the Agentic account with money you're prepared to risk —
   it's isolated from your primary Robinhood account.
3. Implement `RobinhoodBroker` against the MCP tools documented at
   robinhood.com/us/en/support/articles/trading-with-your-agent,
   confirming the connection can run unattended on a server (this is
   unverified today — Robinhood's MCP is built primarily for
   interactive agent sessions).
4. Run it in parallel against paper mode for a while, comparing
   decisions, before setting `TRADING_MODE=live`.
5. Never flip modes automatically — always a deliberate `.env` change
   + restart.
