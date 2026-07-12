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

`RobinhoodBroker` is implemented (`app/brokers/robinhood_broker.py`,
`app/brokers/robinhood_oauth.py`), but stays inert behind two gates:
`TRADING_MODE` (still `paper`) and `LIVE_DRY_RUN` (default `true`,
which stops every order after Robinhood's own pre-trade simulation —
see `CHANGELOG.md` and the mapping comment at the top of
`robinhood_broker.py`).

1. Request Robinhood Agentic Trading access
   (robinhood.com/us/en/agentic-trading) and open the dedicated
   Agentic account. Fund only that account with money you're prepared
   to risk — it's isolated from your primary Robinhood account.

2. Rebuild after pulling in the Phase 2 code (also publishes port 3030
   for the one-time OAuth callback, and mounts `./secrets` for the
   token file):

   ```bash
   docker compose up -d --build
   ```

3. One-time interactive login — **this needs a real browser, run it
   yourself**, it can't be scripted end-to-end:

   ```bash
   docker compose exec day-trader-app python scripts/robinhood_oauth_setup.py
   ```

   It prints a URL to open and log into Robinhood with. If your
   browser isn't on this devbox, forward the callback port first:

   ```bash
   ssh -L 3030:localhost:3030 devbox
   ```

   Tokens get saved to `secrets/robinhood_tokens.json` (git-ignored,
   survives rebuilds via the bind mount). Re-run this any time the bot
   auto-pauses with a `RobinhoodAuthError` reason on the dashboard.

4. Verify the tool/field-name assumptions baked into
   `robinhood_broker.py` actually match what Robinhood's MCP returns,
   **before** trusting it with money:

   ```bash
   docker compose exec day-trader-app python scripts/robinhood_list_tools.py
   ```

   Compare its output (tool names, input/output schemas) against the
   mapping comment and the `.get(...)` field-name guesses in
   `app/brokers/robinhood_broker.py`. Fix anything that doesn't match
   before going further — this is expected to need at least one
   iteration, not a formality.

5. Run it in parallel against paper mode for a while with
   `TRADING_MODE=live` and `LIVE_DRY_RUN=true`, comparing the
   `[DRY RUN]` decisions it logs against what paper mode would have
   done, before ever setting `LIVE_DRY_RUN=false`.

6. Never flip `TRADING_MODE` or `LIVE_DRY_RUN` automatically — always
   a deliberate `.env` change + restart.
