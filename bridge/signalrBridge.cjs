// signalrBridge.cjs
const express = require('express');
const { HubConnectionBuilder, HttpTransportType, LogLevel } = require('@microsoft/signalr');
const axios = require('axios'); // Used for sending events to n8n webhooks

const PORT = 10000;
const ACCOUNT_ID = process.env.ACCOUNT_ID; // Your TopstepX Account ID
const CONTRACT_ID = process.env.CONTRACT_ID; // Your TopstepX Contract ID (e.g., CON.F.US.ENQ.M25)

const USER_HUB_URL = `https://rtc.topstepx.com/hubs/user`;
const MARKET_HUB_URL = `https://rtc.topstepx.com/hubs/market`;

// --- n8n Webhook URLs (CONFIGURE THESE) ---
const N8N_USER_FILL_WEBHOOK_URL = process.env.N8N_USER_FILL_WEBHOOK_URL || 'https://your-n8n-url/webhook/topstepx-user-fill'; // For GatewayUserTrade, etc.
const N8N_MARKET_DATA_WEBHOOK_URL = process.env.N8N_MARKET_DATA_WEBHOOK_URL || 'https://your-n8n-url/webhook/topstepx-market-data'; // For GatewayQuote, GatewayTrade, etc.

let currentTopstepToken = null;
let userHubConnection = null;
let marketHubConnection = null;

// --- State Flags ---
let userHubConnected = false;
let marketHubConnected = false;

// --- SignalR Connection and Subscription Functions ---

async function startUserHubConnection() {
  console.log('[Bridge] Initializing User Hub SignalR Bridge...');

  userHubConnection = new HubConnectionBuilder()
    .withUrl(USER_HUB_URL, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => {
        console.log('[Bridge][UserHub] Providing access token:', currentTopstepToken ? '[REDACTED]' : '[MISSING]');
        return currentTopstepToken;
      },
    })
    .configureLogging(LogLevel.Debug)
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(30000, retryContext.previousRetryCount * 2000); // Max 30s delay
        console.log(`[Bridge][UserHub] Retry in ${delay}ms`);
        return delay;
      }
    })
    .build();

  userHubConnection.onclose(error => {
    userHubConnected = false;
    console.error('[Bridge][UserHub] Connection closed.', error || 'No error specified');
  });

  userHubConnection.onreconnected(() => {
    console.log('[Bridge][UserHub] Reconnected! Re-subscribing...');
    subscribeToUserHub();
  });

  // --- User Hub Event Listeners and Forwarding ---
  userHubConnection.on("GatewayUserTrade", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserTrade.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserTrade', data: data });
  });
  userHubConnection.on("GatewayUserOrder", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserOrder.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserOrder', data: data }); // Or create a separate webhook for orders if needed
  });
  userHubConnection.on("GatewayUserPosition", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserPosition.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserPosition', data: data }); // Or separate webhook for positions
  });
  userHubConnection.on("GatewayUserAccount", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserAccount.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserAccount', data: data }); // Or separate webhook for accounts
  });


  try {
    console.log('[Bridge][UserHub] Starting connection...');
    await userHubConnection.start();
    userHubConnected = true;
    console.log('[Bridge][UserHub] ✅ User Hub SignalR Connected successfully.');
    // Delay subscription slightly to ensure connection is fully established
    setTimeout(() => subscribeToUserHub(), 500); 
  } catch (err) {
    userHubConnected = false;
    console.error('[Bridge][UserHub] Failed to connect:', err);
    // Retry connection after a delay
    setTimeout(startUserHubConnection, 5000); 
  }
}

async function startMarketHubConnection() {
  console.log('[Bridge] Initializing Market Hub SignalR Bridge...');

  marketHubConnection = new HubConnectionBuilder()
    .withUrl(MARKET_HUB_URL, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => {
        console.log('[Bridge][MarketHub] Providing access token:', currentTopstepToken ? '[REDACTED]' : '[MISSING]');
        return currentTopstepToken;
      },
    })
    .configureLogging(LogLevel.Debug)
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(30000, retryContext.previousRetryCount * 2000); // Max 30s delay
        console.log(`[Bridge][MarketHub] Retry in ${delay}ms`);
        return delay;
      }
    })
    .build();

  marketHubConnection.onclose(error => {
    marketHubConnected = false;
    console.error('[Bridge][MarketHub] Connection closed.', error || 'No error specified');
  });

  marketHubConnection.onreconnected(() => {
    console.log('[Bridge][MarketHub] Reconnected! Re-subscribing...');
    subscribeToMarketHub();
  });

  // --- Market Hub Event Listeners and Forwarding ---
  marketHubConnection.on("GatewayQuote", (data) => {
    // console.log('[Bridge][MarketHub] Received GatewayQuote.'); // Too verbose for production logs
    sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayQuote', data: data });
  });
  marketHubConnection.on("GatewayTrade", (data) => {
    // console.log('[Bridge][MarketHub] Received GatewayTrade.'); // Too verbose for production logs
    sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayTrade', data: data });
  });
  marketHubConnection.on("GatewayDepth", (data) => {
    // console.log('[Bridge][MarketHub] Received GatewayDepth.'); // Too verbose for production logs
    sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayDepth', data: data });
  });

  try {
    console.log('[Bridge][MarketHub] Starting connection...');
    await marketHubConnection.start();
    marketHubConnected = true;
    console.log('[Bridge][MarketHub] ✅ Market Hub SignalR Connected successfully.');
    setTimeout(() => subscribeToMarketHub(), 500);
  } catch (err) {
    marketHubConnected = false;
    console.error('[Bridge][MarketHub] Failed to connect:', err);
    setTimeout(startMarketHubConnection, 5000);
  }
}

// --- Subscription Helpers ---
function subscribeToUserHub() {
  if (!userHubConnected) {
    console.warn('[Bridge][UserHub] Cannot subscribe, User Hub not connected.');
    return;
  }
  if (!ACCOUNT_ID) {
    console.error('[Bridge][UserHub] ACCOUNT_ID is not defined. Cannot subscribe to user data.');
    return;
  }

  console.log(`[Bridge][UserHub] Invoking User Hub subscriptions for Account ID: ${ACCOUNT_ID}...`);
  userHubConnection.invoke('SubscribeAccounts', ACCOUNT_ID)
    .then(() => console.log('[Bridge][UserHub] SubscribeAccounts invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeAccounts error:', err));
  userHubConnection.invoke('SubscribeOrders', ACCOUNT_ID)
    .then(() => console.log('[Bridge][UserHub] SubscribeOrders invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeOrders error:', err));
  userHubConnection.invoke('SubscribePositions', ACCOUNT_ID)
    .then(() => console.log('[Bridge][UserHub] SubscribePositions invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribePositions error:', err));
  userHubConnection.invoke('SubscribeTrades', ACCOUNT_ID) // This is where fills come from
    .then(() => console.log('[Bridge][UserHub] SubscribeTrades invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeTrades error:', err));
}

function subscribeToMarketHub() {
  if (!marketHubConnected) {
    console.warn('[Bridge][MarketHub] Cannot subscribe, Market Hub not connected.');
    return;
  }
  if (!CONTRACT_ID) {
    console.error('[Bridge][MarketHub] CONTRACT_ID is not defined. Cannot subscribe to market data.');
    return;
  }

  console.log(`[Bridge][MarketHub] Invoking Market Hub subscriptions for Contract ID: ${CONTRACT_ID}...`);
  marketHubConnection.invoke('SubscribeContractQuotes', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractQuotes invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractQuotes error:', err));
  marketHubConnection.invoke('SubscribeContractTrades', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractTrades invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractTrades error:', err));
  marketHubConnection.invoke('SubscribeContractMarketDepth', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractMarketDepth invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractMarketDepth error:', err));
}

// --- Event Forwarding to n8n ---
async function sendEventToN8n(webhookUrl, payload) {
    try {
        const response = await axios.post(webhookUrl, payload, {
            headers: { 'Content-Type': 'application/json' }
        });
        // console.log(`[Bridge][N8N] Event sent to ${webhookUrl}: ${response.status}`); // Too verbose for production logs
    } catch (error) {
        console.error(`[Bridge][N8N] Error sending event to ${webhookUrl}:`, error.message);
        // Log more details about axios error if needed
        if (error.response) {
            console.error(`[Bridge][N8N] Response status: ${error.response.status}, data: ${JSON.stringify(error.response.data)}`);
        }
    }
}

// --- Express App Setup ---
const app = express();
app.use(express.json()); // Middleware to parse JSON body

// --- REST Endpoints ---
app.post('/update-token', (req, res) => {
  const { access_token } = req.body; // Expects 'access_token' in the body
  if (access_token) {
    console.log('[Bridge][HTTP] Token updated via /update-token');
    currentTopstepToken = access_token;

    // Disconnect any existing connections first to ensure fresh start with new token
    if (userHubConnection && userHubConnection.state !== 'Disconnected') {
        userHubConnection.stop();
        userHubConnection = null;
    }
    if (marketHubConnection && marketHubConnection.state !== 'Disconnected') {
        marketHubConnection.stop();
        marketHubConnection = null;
    }

    // Start both hub connections with the new token
    startUserHubConnection();
    startMarketHubConnection();
    
    res.sendStatus(200);
  } else {
    console.warn('[Bridge][HTTP] Token update failed: Missing token');
    res.status(400).send('Missing token');
  }
});

app.get('/health', (req, res) => {
  console.log('[Bridge][HTTP] Health check received');
  res.send({ 
    status: 'Bridge running', 
    userHubConnected: userHubConnected, 
    marketHubConnected: marketHubConnected 
  });
});

// --- Start the Express server ---
app.listen(PORT, () => {
  console.log(`[Bridge][HTTP] Server listening on port ${PORT}. /update-token and /health active.`);
  // No initial connection attempts here. Rely entirely on /update-token from n8n.
  console.log('[Bridge][HTTP] Awaiting token via POST /update-token before starting SignalR...');
});
