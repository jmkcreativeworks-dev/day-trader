"""FastAPI app: monitoring dashboard + small JSON API + background
scheduler bootstrap."""
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import BasicAuthMiddleware
from app.config import settings
from app.db import Base, engine, get_db
from app.models import BotState, RiskConfig, PortfolioSnapshot, Position, AIDecision, Trade
from app.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

# Self-migrating: adds paper_starting_cash to installs that predate it.
# No Alembic - this project has exactly one hand-rolled column migration.
with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS "
        "paper_starting_cash FLOAT DEFAULT 10000"
    ))

if not settings.DASHBOARD_PASSWORD:
    raise RuntimeError(
        "DASHBOARD_PASSWORD is not set - refusing to start. Set "
        "DASHBOARD_USERNAME/DASHBOARD_PASSWORD in .env (see .env.example)."
    )

app = FastAPI(title="day-trader")
app.add_middleware(BasicAuthMiddleware)
templates = Jinja2Templates(directory="app/templates")

_scheduler = None


@app.on_event("startup")
def on_startup():
    global _scheduler
    db = next(get_db())
    if not db.query(BotState).first():
        db.add(BotState(
            mode=settings.TRADING_MODE,
            paper_starting_cash=settings.PAPER_STARTING_CASH,
        ))
    if not db.query(RiskConfig).first():
        db.add(RiskConfig(
            max_position_pct=settings.MAX_POSITION_PCT,
            max_daily_loss_pct=settings.MAX_DAILY_LOSS_PCT,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            allow_options=settings.ALLOW_OPTIONS,
        ))
    db.commit()
    db.close()
    _scheduler = start_scheduler()
    logger.info("day-trader started in %s mode, ticking every %s min",
                settings.TRADING_MODE, settings.TICK_INTERVAL_MINUTES)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    state = db.query(BotState).first()
    risk_cfg = db.query(RiskConfig).first()
    mode = state.mode if state else settings.TRADING_MODE

    positions = db.query(Position).filter(
        Position.mode == mode, Position.quantity > 0
    ).all()

    snapshots = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.mode == mode)
        .order_by(PortfolioSnapshot.timestamp.asc())
        .all()
    )

    decisions = (
        db.query(AIDecision)
        .filter(AIDecision.mode == mode)
        .order_by(AIDecision.timestamp.desc())
        .limit(50)
        .all()
    )

    baseline_cash = state.paper_starting_cash if state else settings.PAPER_STARTING_CASH
    latest_value = snapshots[-1].total_value if snapshots else baseline_cash
    starting_value = snapshots[0].total_value if snapshots else baseline_cash
    total_return_pct = (
        (latest_value - starting_value) / starting_value * 100 if starting_value else 0
    )

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "state": state,
        "risk_cfg": risk_cfg,
        "positions": positions,
        "chart_labels": [s.timestamp.strftime("%m/%d %H:%M") for s in snapshots],
        "chart_values": [s.total_value for s in snapshots],
        "decisions": decisions,
        "latest_value": latest_value,
        "total_return_pct": total_return_pct,
        "now": datetime.now(timezone.utc),
    })


@app.post("/actions/pause")
def pause(db: Session = Depends(get_db)):
    state = db.query(BotState).first()
    state.running = False
    state.paused_reason = "Manually paused from dashboard"
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/actions/resume")
def resume(db: Session = Depends(get_db)):
    state = db.query(BotState).first()
    state.running = True
    state.paused_reason = None
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/actions/risk-config")
def update_risk_config(
    max_position_pct: float = Form(...),
    max_daily_loss_pct: float = Form(...),
    max_open_positions: int = Form(...),
    allow_options: bool = Form(False),
    db: Session = Depends(get_db),
):
    cfg = db.query(RiskConfig).first()
    cfg.max_position_pct = max_position_pct / 100
    cfg.max_daily_loss_pct = max_daily_loss_pct / 100
    cfg.max_open_positions = max_open_positions
    cfg.allow_options = allow_options
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/actions/reset-paper")
def reset_paper(
    new_starting_cash: float = Form(...),
    db: Session = Depends(get_db),
):
    # Delete children before parents: trades reference ai_decisions.
    db.query(Trade).filter(Trade.mode == "paper").delete()
    db.query(Position).filter(Position.mode == "paper").delete()
    db.query(PortfolioSnapshot).filter(PortfolioSnapshot.mode == "paper").delete()
    db.query(AIDecision).filter(AIDecision.mode == "paper").delete()

    state = db.query(BotState).first()
    state.paper_starting_cash = new_starting_cash
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
