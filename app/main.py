"""FastAPI app: monitoring dashboard + small JSON API + background
scheduler bootstrap."""
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import bcrypt
from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import BasicAuthMiddleware
from app.config import settings
from app.db import Base, engine, get_db
from app.models import (
    BotState, RiskConfig, PortfolioSnapshot, Position, AIDecision, Trade, DashboardUser,
)
from app.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

# Self-migrating: adds columns to installs that predate them. No Alembic -
# just hand-rolled ALTER TABLE ... ADD COLUMN IF NOT EXISTS statements.
with engine.begin() as conn:
    conn.execute(text(
        "ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS "
        "paper_starting_cash FLOAT DEFAULT 10000"
    ))
    conn.execute(text(
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS realized_pnl FLOAT"
    ))

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
    if not db.query(DashboardUser).first():
        # Bootstrap only: seeds the first account from .env so a fresh
        # deploy isn't locked out. Once any user exists, DASHBOARD_* env
        # vars are never consulted again - manage users from /users.
        if not settings.DASHBOARD_PASSWORD:
            raise RuntimeError(
                "No dashboard users exist and DASHBOARD_PASSWORD is blank - "
                "refusing to start. Set DASHBOARD_USERNAME/DASHBOARD_PASSWORD "
                "in .env to bootstrap the first account (see .env.example)."
            )
        db.add(DashboardUser(
            username=settings.DASHBOARD_USERNAME,
            password_hash=bcrypt.hashpw(
                settings.DASHBOARD_PASSWORD.encode(), bcrypt.gensalt()
            ).decode(),
        ))
        logger.info("Seeded bootstrap dashboard user '%s' from .env", settings.DASHBOARD_USERNAME)
    db.commit()
    db.close()
    _scheduler = start_scheduler()
    logger.info("day-trader started in %s mode, ticking every %s min",
                settings.TRADING_MODE, settings.TICK_INTERVAL_MINUTES)


def _next_tick_at():
    job = _scheduler.get_job("run_tick") if _scheduler else None
    return job.next_run_time if job else None


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

    trades = (
        db.query(Trade)
        .filter(Trade.mode == mode)
        .order_by(Trade.timestamp.desc())
        .limit(50)
        .all()
    )

    baseline_cash = state.paper_starting_cash if state else settings.PAPER_STARTING_CASH
    latest_value = snapshots[-1].total_value if snapshots else baseline_cash
    starting_value = snapshots[0].total_value if snapshots else baseline_cash
    total_return_pct = (
        (latest_value - starting_value) / starting_value * 100 if starting_value else 0
    )

    next_tick_at = _next_tick_at()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "state": state,
        "risk_cfg": risk_cfg,
        "positions": positions,
        "chart_labels": [s.timestamp.strftime("%m/%d %H:%M") for s in snapshots],
        "chart_values": [s.total_value for s in snapshots],
        "decisions": decisions,
        "trades": trades,
        "latest_value": latest_value,
        "total_return_pct": total_return_pct,
        "next_tick_at": next_tick_at.isoformat() if next_tick_at else None,
        "last_tick_at": state.last_tick_at.isoformat() if state and state.last_tick_at else None,
        "now": datetime.now(timezone.utc),
    })


@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    """Polled by the dashboard's JS to auto-reload the page right after a
    tick actually finishes (not just on a fixed timer, since tick duration
    varies with watchlist size/API latency)."""
    state = db.query(BotState).first()
    return {
        "last_tick_at": state.last_tick_at.isoformat() if state and state.last_tick_at else None,
        "next_tick_at": (lambda t: t.isoformat() if t else None)(_next_tick_at()),
    }


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


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(DashboardUser).order_by(DashboardUser.created_at.asc()).all()
    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "current_username": request.state.username,
        "error": request.query_params.get("error"),
    })


@app.post("/users/add")
def add_user(
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    username = username.strip()
    errors = []
    if not username:
        errors.append("Username is required.")
    elif db.query(DashboardUser).filter(DashboardUser.username == username).first():
        errors.append(f"Username '{username}' already exists.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    elif password != confirm_password:
        errors.append("Passwords do not match.")

    if errors:
        return RedirectResponse(f"/users?error={quote(' '.join(errors))}", status_code=303)

    db.add(DashboardUser(
        username=username,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
    ))
    db.commit()
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/delete")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    target = db.query(DashboardUser).filter(DashboardUser.id == user_id).first()
    if not target:
        return RedirectResponse("/users", status_code=303)

    if target.username == request.state.username:
        return RedirectResponse(
            f"/users?error={quote('Cannot delete the account you are currently logged in as.')}",
            status_code=303,
        )

    if db.query(DashboardUser).count() <= 1:
        return RedirectResponse(
            f"/users?error={quote('Cannot delete the last remaining user.')}",
            status_code=303,
        )

    db.delete(target)
    db.commit()
    return RedirectResponse("/users", status_code=303)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
