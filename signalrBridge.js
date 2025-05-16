const { HubConnectionBuilder, HttpTransportType } = require('@microsoft/signalr');
const axios = require('axios');

// CONFIG — Fill in your values
const AUTH_TOKEN = process.env.TOPSTEP_TOKEN;  // Set via env var
const CONTRACT_ID = process.env.CONTRACT_ID || "CON.F.US.EP.M25";
const PYTHON_BACKEND_URL = "http://localhost:10000/bridge_trade_event"; // Update if needed

if (!AUTH_TOKEN) {
  console.error("❌ TOPSTEP_TOKEN environment variable is not set.");
  process.exit(1);
}

let connectionStarted = false;

async function startBridge() {
  const marketHubUrl = `https://rtc.topstepx.com/hubs/user?access_token=${AUTH_TOKEN}`;

  const connection = new HubConnectionBuilder()
    .withUrl(marketHubUrl, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => AUTH_TOKEN,
    })
    .withAutomaticReconnect()
    .build();

  connection.on("GatewayUserTrade", async (args) => {
    console.log("[Bridge] Trade:", args);
    try {
      await axios.post(PYTHON_BACKEND_URL, {
        contractId: CONTRACT_ID,
        tradeData: args
      });
    } catch (err) {
      console.error("[Bridge] Failed to POST to FastAPI:", err.message);
    }
  });

  connection.onreconnected(() => {
    console.log("[Bridge] Reconnected.");
  });

  try {
    await connection.start();
    connectionStarted = true;
    console.log(`[Bridge] Connected and listening for GatewayUserTrade on ${CONTRACT_ID}`);
  } catch (err) {
    console.error("[Bridge] Connection error:", err.message);
    process.exit(1);
  }
}

startBridge();
