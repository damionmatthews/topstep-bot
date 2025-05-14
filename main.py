from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from datetime import datetime
import httpx
import os
import json

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

# Strategy storage path
STRATEGY_PATH = "strategies.json"

# Load or initialize strategies
if os.path.exists(STRATEGY_PATH):
    with open(STRATEGY_PATH, "r") as f:
        strategies = json.load(f)
else:
    strategies = {
        "default": {
            "MAX_DAILY_LOSS": -1200,
            "MAX_DAILY_PROFIT": 2000,
            "MAX_TRADE_LOSS": -350,
            "MAX_TRADE_PROFIT": 450,
            "CONTRACT_SYMBOL": "NQ",
            "TRADE_SIZE": 1
        }
    }
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

current_strategy = "default"
config = strategies[current_strategy]

daily_pnl = 0.0
trade_active = False
entry_price = None
trade_time = None
current_signal = None

# --- DATA MODEL ---
class SignalAlert(BaseModel):
    signal: str
    ticker: str
    time: str

class StatusResponse(BaseModel):
    trade_active: bool
    entry_price: float | None
    trade_time: str | None
    current_signal: str | None
    daily_pnl: float

# --- HELPER FUNCTIONS ---
def save_strategies():
    with open(STRATEGY_PATH, "w") as f:
        json.dump(strategies, f, indent=2)

async def place_order(direction: str):
    side = "buy" if direction == "long" else "sell"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://gateway-api.projectx.com/api/orders",
            headers={"Authorization": f"Bearer {TOPSTEP_API_KEY}"},
            json={
                "accountId": ACCOUNT_ID,
                "symbol": config["CONTRACT_SYMBOL"],
                "qty": config["TRADE_SIZE"],
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

async def check_and_close_trade(current_price: float):
    global trade_active, entry_price, daily_pnl, current_signal
    if not trade_active or entry_price is None:
        return

    pnl = (current_price - entry_price) * (1 if current_signal == 'long' else -1) * 20
    if pnl >= config["MAX_TRADE_PROFIT"] or pnl <= config["MAX_TRADE_LOSS"]:
        await close_position()
        daily_pnl += pnl
        trade_active = False
        print(f"Trade exited at {current_price}, PnL: {pnl}")

    if daily_pnl >= config["MAX_DAILY_PROFIT"] or daily_pnl <= config["MAX_DAILY_LOSS"]:
        trade_active = False
        print("Trading halted for the day")

async def close_position():
    side = "sell" if current_signal == "long" else "buy"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://gateway-api.projectx.com/api/orders",
            headers={"Authorization": f"Bearer {TOPSTEP_API_KEY}"},
            json={
                "accountId": ACCOUNT_ID,
                "symbol": config["CONTRACT_SYMBOL"],
                "qty": config["TRADE_SIZE"],
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

# --- MAIN ENDPOINTS ---
@app.post("/webhook/{strategy}")
async def receive_alert_strategy(strategy: str, alert: SignalAlert):
    global trade_active, entry_price, current_signal, trade_time, daily_pnl, config

    if strategy not in strategies:
        return {"status": "error", "reason": f"Strategy '{strategy}' not found"}

    config = strategies[strategy]

    if daily_pnl >= config["MAX_DAILY_PROFIT"] or daily_pnl <= config["MAX_DAILY_LOSS"]:
        return {"status": "halted", "reason": "daily limit reached"}

    if trade_active:
        return {"status": "ignored", "reason": "trade already active"}

    print(f"[{strategy}] Received {alert.signal} signal for {alert.ticker} at {alert.time}")

    try:
        result = await place_order(alert.signal)
        entry_price = result.get("fillPrice", 0.0)
        trade_active = True
        current_signal = alert.signal
        trade_time = datetime.utcnow()
        return {"status": "trade placed", "entry_price": entry_price}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/check")
async def check_price():
    if not trade_active:
        return {"status": "no active trade"}

    current_price = entry_price + 25
    await check_and_close_trade(current_price)
    return {"status": "checked", "price": current_price}

@app.get("/status", response_model=StatusResponse)
async def get_status():
    return StatusResponse(
        trade_active=trade_active,
        entry_price=entry_price,
        trade_time=trade_time.isoformat() if trade_time else None,
        current_signal=current_signal,
        daily_pnl=daily_pnl
    )

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    strategy_options = "".join([f'<option value="{name}" {"selected" if name == current_strategy else ""}>{name}</option>' for name in strategies])
    rows = "".join([f"<tr><td>{name}</td><td><a href='/delete?strategy={name}'>Delete</a> | <a href='/clone?strategy={name}'>Clone</a></td></tr>" for name in strategies])
    current = strategies[current_strategy]
    html_content = f"""
    <html>
    <head><title>Trading Bot Dashboard</title></head>
    <body>
        <h1>Topstep Bot Dashboard</h1>
        <form action="/config" method="post">
            <label>Strategy: <select name="strategy">{strategy_options}</select></label><br>
            <label>Contract Symbol: <input name="symbol" value="{current['CONTRACT_SYMBOL']}" /></label><br>
            <label>Trade Size: <input name="size" type="number" value="{current['TRADE_SIZE']}" /></label><br>
            <label>Max Trade Loss: <input name="max_trade_loss" type="number" value="{current['MAX_TRADE_LOSS']}" /></label><br>
            <label>Max Trade Profit: <input name="max_trade_profit" type="number" value="{current['MAX_TRADE_PROFIT']}" /></label><br>
            <label>Max Daily Loss: <input name="max_daily_loss" type="number" value="{current['MAX_DAILY_LOSS']}" /></label><br>
            <label>Max Daily Profit: <input name="max_daily_profit" type="number" value="{current['MAX_DAILY_PROFIT']}" /></label><br>
            <button type="submit">Update Config</button>
        </form>
        <h2>Strategies</h2>
        <table border='1'><tr><th>Name</th><th>Actions</th></tr>{rows}</table>
        <hr>
        <p><strong>Trade Active:</strong> {trade_active}</p>
        <p><strong>Entry Price:</strong> {entry_price}</p>
        <p><strong>Trade Time:</strong> {trade_time}</p>
        <p><strong>Current Signal:</strong> {current_signal}</p>
        <p><strong>Daily PnL:</strong> {daily_pnl}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/config")
async def update_config(
    strategy: str = Form(...),
    symbol: str = Form(...),
    size: int = Form(...),
    max_trade_loss: float = Form(...),
    max_trade_profit: float = Form(...),
    max_daily_loss: float = Form(...),
    max_daily_profit: float = Form(...)
):
    global config, current_strategy
    if strategy not in strategies:
        strategies[strategy] = {}
    strategies[strategy].update({
        "CONTRACT_SYMBOL": symbol,
        "TRADE_SIZE": size,
        "MAX_TRADE_LOSS": max_trade_loss,
        "MAX_TRADE_PROFIT": max_trade_profit,
        "MAX_DAILY_LOSS": max_daily_loss,
        "MAX_DAILY_PROFIT": max_daily_profit
    })
    save_strategies()
    current_strategy = strategy
    config = strategies[current_strategy]
    return RedirectResponse(url="/", status_code=303)

@app.get("/delete")
async def delete_strategy(strategy: str):
    global current_strategy, config
    if strategy in strategies and strategy != "default":
        del strategies[strategy]
        save_strategies()
        current_strategy = "default"
        config = strategies[current_strategy]
    return RedirectResponse(url="/", status_code=303)

@app.get("/clone")
async def clone_strategy(strategy: str):
    if strategy in strategies:
        new_name = f"{strategy}_copy"
        count = 1
        while new_name in strategies:
            new_name = f"{strategy}_copy{count}"
            count += 1
        strategies[new_name] = dict(strategies[strategy])
        save_strategies()
    return RedirectResponse(url="/", status_code=303)
    
@app.get("/", response_class=HTMLResponse)
async def root_dashboard():
    return await dashboard_menu()

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_menu():
    return HTMLResponse("""
    <html>
    <head><title>Bot Dashboard</title></head>
    <body>
        <h1>Topstep Bot Dashboard</h1>
        <ul>
            <li><a href="/logs/trades">Trade History</a></li>
            <li><a href="/logs/alerts">Alert Log</a></li>
            <li><a href="/toggle">Toggle Trading</a></li>
        </ul>
    </body>
    </html>
    """)

@app.get("/logs/trades", response_class=HTMLResponse)
async def trade_log_view():
    if os.path.exists(TRADE_LOG_PATH):
        with open(TRADE_LOG_PATH, "r") as f:
            trades = json.load(f)
    else:
        trades = []
    summary = {
        "total_trades": len(trades),
        "wins": sum(1 for t in trades if t["event"] == "exit" and t.get("pnl", 0) > 0),
        "losses": sum(1 for t in trades if t["event"] == "exit" and t.get("pnl", 0) <= 0),
        "avg_pnl": round(sum(t.get("pnl", 0) for t in trades if t["event"] == "exit") / max(1, sum(1 for t in trades if t["event"] == "exit")), 2)
    }
    rows = "".join([
        f"<tr><td>{t['timestamp']}</td><td>{t['event']}</td><td>{t.get('signal','')}</td><td>{t.get('entry_price','')}</td><td>{t.get('exit_price','')}</td><td>{t.get('pnl','')}</td></tr>"
        for t in trades
    ])
    return HTMLResponse(f"""
    <h1>Trade History</h1>
    <table border='1'><tr><th>Time</th><th>Event</th><th>Signal</th><th>Entry</th><th>Exit</th><th>PnL</th></tr>{rows}</table>
    <h2>Performance Summary</h2>
    <pre>{json.dumps(summary, indent=2)}</pre>
    """)

@app.get("/logs/alerts", response_class=HTMLResponse)
async def alert_log_view():
    if os.path.exists(ALERT_LOG_PATH):
        with open(ALERT_LOG_PATH, "r") as f:
            alerts = json.load(f)
    else:
        alerts = []
    rows = "".join([
        f"<tr><td>{a['time']}</td><td>{a['signal']}</td><td>{a['ticker']}</td></tr>"
        for a in alerts
    ])
    return HTMLResponse(f"""
    <h1>Alert Log</h1>
    <table border='1'><tr><th>Time</th><th>Signal</th><th>Ticker</th></tr>{rows}</table>
    """)

    
