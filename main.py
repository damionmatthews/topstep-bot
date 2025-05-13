app = FastAPI()

from fastapi import FastAPI, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import httpx
import os

app = FastAPI()

# --- ENVIRONMENT CONFIG ---
TOPSTEP_API_KEY = os.getenv("hUcaJ5J88F92wlp2J0byZPRicRQwrW5EtPTNSkfZSac=")
ACCOUNT_ID = os.getenv("305")  # Your ProjectX account ID
MAX_DAILY_LOSS = -1200
MAX_DAILY_PROFIT = 2000
MAX_TRADE_LOSS = -350
MAX_TRADE_PROFIT = 450
CONTRACT_SYMBOL = "NQ"
TRADE_SIZE = 1

# --- IN-MEMORY STATE ---
daily_pnl = 0.0
trade_active = False
entry_price = None
trade_time = None

# --- DATA MODEL ---
class SignalAlert(BaseModel):
    signal: str  # 'long' or 'short'
    ticker: str
    time: str

# --- HELPER FUNCTIONS ---
async def place_order(direction: str):
    side = "buy" if direction == "long" else "sell"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.projectx.com/orders",  # Replace with actual ProjectX endpoint
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
    global trade_active, entry_price, daily_pnl
    if not trade_active or entry_price is None:
        return

    pnl = (current_price - entry_price) * (1 if current_signal == 'long' else -1) * 20  # NQ tick value * ticks

    # Close if target or stop hit
    if pnl >= MAX_TRADE_PROFIT or pnl <= MAX_TRADE_LOSS:
        await close_position()
        daily_pnl += pnl
        trade_active = False
        print(f"Trade exited at {current_price}, PnL: {pnl}")

    # Check daily PnL stop
    if daily_pnl >= MAX_DAILY_PROFIT or daily_pnl <= MAX_DAILY_LOSS:
        trade_active = False
        print("Trading halted for the day")

async def close_position():
    side = "sell" if current_signal == "long" else "buy"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.projectx.com/orders",
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

# --- MAIN ENDPOINT ---
@app.post("/webhook")
async def receive_alert(alert: SignalAlert):
    global trade_active, entry_price, current_signal, trade_time

    if daily_pnl >= MAX_DAILY_PROFIT or daily_pnl <= MAX_DAILY_LOSS:
        return {"status": "halted", "reason": "daily limit reached"}

    if trade_active:
        return {"status": "ignored", "reason": "trade already active"}

    print(f"Received {alert.signal} signal for {alert.ticker} at {alert.time}")

    result = await place_order(alert.signal)
    entry_price = result.get("fillPrice")  # Adjust based on actual API response
    trade_active = True
    current_signal = alert.signal
    trade_time = datetime.utcnow()

    return {"status": "trade placed", "entry_price": entry_price}

# --- Scheduled task simulation (e.g., every 15s) ---
@app.get("/check")
async def check_price():
    if not trade_active:
        return {"status": "no active trade"}

    # Simulated price fetch (replace with real quote API)
    current_price = entry_price + 25  # Simulate price move
    await check_and_close_trade(current_price)

    return {"status": "checked", "price": current_price}
