from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime
import httpx
import os

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- ENVIRONMENT CONFIG ---
TOPSTEP_API_KEY = os.getenv("TOPSTEP_API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
MAX_DAILY_LOSS = -1200
MAX_DAILY_PROFIT = 2000
MAX_TRADE_LOSS = -350
MAX_TRADE_PROFIT = 450
CONTRACT_SYMBOL = "NQ"
TRADE_SIZE = 1

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
async def place_order(direction: str):
    side = "buy" if direction == "long" else "sell"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://gateway-api.projectx.com/api/orders",
            headers={"Authorization": f"Bearer {TOPSTEP_API_KEY}"},
            json={
                "accountId": ACCOUNT_ID,
                "symbol": CONTRACT_SYMBOL,
                "qty": TRADE_SIZE,
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
    if pnl >= MAX_TRADE_PROFIT or pnl <= MAX_TRADE_LOSS:
        await close_position()
        daily_pnl += pnl
        trade_active = False
        print(f"Trade exited at {current_price}, PnL: {pnl}")

    if daily_pnl >= MAX_DAILY_PROFIT or daily_pnl <= MAX_DAILY_LOSS:
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
                "symbol": CONTRACT_SYMBOL,
                "qty": TRADE_SIZE,
                "side": side,
                "type": "market"
            }
        )
        response.raise_for_status()
        return response.json()

# --- MAIN ENDPOINTS ---
@app.post("/webhook")
async def receive_alert(alert: SignalAlert):
    global trade_active, entry_price, current_signal, trade_time, daily_pnl

    if daily_pnl >= MAX_DAILY_PROFIT or daily_pnl <= MAX_DAILY_LOSS:
        return {"status": "halted", "reason": "daily limit reached"}

    if trade_active:
        return {"status": "ignored", "reason": "trade already active"}

    print(f"Received {alert.signal} signal for {alert.ticker} at {alert.time}")

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
    html_content = f"""
    <html>
    <head><title>Trading Bot Dashboard</title></head>
    <body>
        <h1>Topstep Bot Dashboard</h1>
        <p><strong>Trade Active:</strong> {trade_active}</p>
        <p><strong>Entry Price:</strong> {entry_price}</p>
        <p><strong>Trade Time:</strong> {trade_time}</p>
        <p><strong>Current Signal:</strong> {current_signal}</p>
        <p><strong>Daily PnL:</strong> {daily_pnl}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
