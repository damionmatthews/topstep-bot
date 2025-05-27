// signalrBridge.cjs
const express = require('express');
const { HubConnectionBuilder, HttpTransportType, LogLevel } = require('@microsoft/signalr');
const axios = require('axios'); // Used for sending events to n8n webhooks

const PORT = 10000;
const ACCOUNT_ID = process.env.ACCOUNT_ID; // Your TopstepX Account ID from Render Environment
const CONTRACT_ID = process.env.CONTRACT_ID; // Your TopstepX Contract ID (e.g., CON.F.US.ENQ.M25) from Render Environment

const USER_HUB_URL = `https://rtc.topstepx.com/hubs/user`;
const MARKET_HUB_URL = `https://rtc.topstepx.com/hubs/market`;

// --- n8n Webhook URLs (CONFIGURE THESE AS RENDER ENVIRONMENT VARIABLES) ---
// Define these in your Render service's Environment variables.
// Example: N8N_USER_FILL_WEBHOOK_URL=https://your-n8n-domain/webhook/topstepx-user-fill
// Example: N8N_MARKET_DATA_WEBHOOK_URL=https://your-n8n-domain/webhook/topstepx-market-data
const N8N_USER_FILL_WEBHOOK_URL = process.env.N8N_USER_FILL_WEBHOOK_URL;
const N8N_MARKET_DATA_WEBHOOK_URL = process.env.N8N_MARKET_DATA_WEBHOOK_URL;

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
        // Provide the current token for initial connection and reconnection
        // console.log('[Bridge][UserHub] Providing access token:', currentTopstepToken ? '[REDACTED]' : '[MISSING]'); // Too verbose
        return currentTopstepToken;
      },
    })
    .configureLogging(LogLevel.Information) // LogLevel.Debug for more verbose logs
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(30000, retryContext.previousRetryCount * 2000); // Max 30s delay
        console.log(`[Bridge][UserHub] Attempting reconnect in ${delay}ms (attempt ${retryContext.previousRetryCount + 1})`);
        return delay;
      }
    })
    .build();

  // --- User Hub Event Listeners (Must be set BEFORE .start()) ---
  userHubConnection.onclose(error => {
    userHubConnected = false;
    console.error('[Bridge][UserHub] Connection closed.', error || 'No error specified');
  });

  userHubConnection.onreconnected(() => {
    console.log('[Bridge][UserHub] Reconnected! Re-subscribing...');
    subscribeToUserHub(); // Re-subscribe on reconnected
  });

  // Event handlers for data received from User Hub
  userHubConnection.on("GatewayUserTrade", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserTrade.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserTrade', data: data });
  });
  userHubConnection.on("GatewayUserOrder", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserOrder.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserOrder', data: data }); // Using same webhook for simplicity
  });
  userHubConnection.on("GatewayUserPosition", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserPosition.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserPosition', data: data }); // Using same webhook for simplicity
  });
  userHubConnection.on("GatewayUserAccount", (data) => {
    console.log('[Bridge][UserHub] Received GatewayUserAccount.');
    sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserAccount', data: data }); // Using same webhook for simplicity
  });


  try {
    console.log('[Bridge][UserHub] Starting connection...');
    await userHubConnection.start(); // Wait for the connection to fully start and handshake
    userHubConnected = true;
    console.log('[Bridge][UserHub] ✅ User Hub SignalR Connected successfully.');
    // --- CALL SUBSCRIBE DIRECTLY AFTER START() RESOLVES ---
    subscribeToUserHub(); // Call immediately, as start() usually implies handshake complete
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
        // console.log('[Bridge][MarketHub] Providing access token:', currentTopstepToken ? '[REDACTED]' : '[MISSING]'); // Too verbose
        return currentTopstepToken;
      },
    })
    .configureLogging(LogLevel.Information) // LogLevel.Debug for more verbose logs
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(30000, retryContext.previousRetryCount * 2000); // Max 30s delay
        console.log(`[Bridge][MarketHub] Attempting reconnect in ${delay}ms (attempt ${retryContext.previousRetryCount + 1})`);
        return delay;
      }
    })
    .build();

  // --- Market Hub Event Listeners (Must be set BEFORE .start()) ---
  marketHubConnection.onclose(error => {
    marketHubConnected = false;
    console.error('[Bridge][MarketHub] Connection closed.', error || 'No error specified');
  });

  marketHubConnection.onreconnected(() => {
    console.log('[Bridge][MarketHub] Reconnected! Re-subscribing...');
    subscribeToMarketHub();
  });

  // Event handlers for data received from Market Hub
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
    await marketHubConnection.start(); // Wait for the connection to fully start and handshake
    marketHubConnected = true;
    console.log('[Bridge][MarketHub] ✅ Market Hub SignalR Connected successfully.');
    // --- CALL SUBSCRIBE DIRECTLY AFTER START() RESOLVES ---
    subscribeToMarketHub();
  } catch (err) {
    marketHubConnected = false;
    console.error('[Bridge][MarketHub] Failed to connect:', err);
    setTimeout(startMarketHubConnection, 5000);
  }
}

// --- Subscription Helper Functions ---
function subscribeToUserHub() {
  if (!userHubConnected) {
    console.warn('[Bridge][UserHub] Cannot subscribe, User Hub not connected.');
    return;
  }
  if (!ACCOUNT_ID) {
    console.error('[Bridge][UserHub] ACCOUNT_ID is not defined in environment variables. Cannot subscribe to user data.');
    return;
  }

  console.log(`[Bridge][UserHub] Invoking User Hub subscriptions for Account ID: ${ACCOUNT_ID}...`);
  userHubConnection.invoke('SubscribeAccounts') // ProjectX docs may suggest ACCOUNT_ID, but general method doesn't always require it for all types
    .then(() => console.log('[Bridge][UserHub] SubscribeAccounts invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeAccounts error:', err));
  userHubConnection.invoke('SubscribeOrders', ACCOUNT_ID) // Requires Account ID as per ProjectX docs
    .then(() => console.log('[Bridge][UserHub] SubscribeOrders invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeOrders error:', err));
  userHubConnection.invoke('SubscribePositions', ACCOUNT_ID) // Requires Account ID as per ProjectX docs
    .then(() => console.log('[Bridge][UserHub] SubscribePositions invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribePositions error:', err));
  userHubConnection.invoke('SubscribeTrades', ACCOUNT_ID) // This is where fills come from, requires Account ID
    .then(() => console.log('[Bridge][UserHub] SubscribeTrades invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeTrades error:', err));
}

function subscribeToMarketHub() {
  if (!marketHubConnected) {
    console.warn('[Bridge][MarketHub] Cannot subscribe, Market Hub not connected.');
    return;
  }
  if (!CONTRACT_ID) {
    console.error('[Bridge][MarketHub] CONTRACT_ID is not defined in environment variables. Cannot subscribe to market data.');
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
    if (!webhookUrl) {
        console.error(`[Bridge][N8N] Webhook URL not defined for payload type: ${payload.type}. Skipping forwarding.`);
        return;
    }
    try {
        const response = await axios.post(webhookUrl, payload, {
            headers: { 'Content-Type': 'application/json' }
        });
        if (!response.ok) { // axios throws an error for 4xx/5xx responses, so this check might not be hit if error is thrown
             console.error(`[Bridge][N8N] Failed to send event to ${webhookUrl}: ${response.status} ${response.statusText} - Response: ${JSON.stringify(response.data)}`);
        } else {
             // console.log(`[Bridge][N8N] Event sent to ${webhookUrl}: ${response.status}`); // Too verbose for production logs
        }
    } catch (error) {
        console.error(`[Bridge][N8N] Error sending event to ${webhookUrl}:`, error.message);
        if (error.response) { // Axios error has a response object
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
    // (This is important to prevent using a stale token)
    if (userHubConnection && userHubConnection.state !== 'Disconnected') {
        userHubConnection.stop();
        userHubConnection = null; // Clear reference to allow new connection
    }
    if (marketHubConnection && marketHubConnection.state !== 'Disconnected') {
        marketHubConnection.stop();
        marketHubConnection = null; // Clear reference to allow new connection
    }

    // Attempt to start both hub connections with the new token
    startUserHubConnection();
    startMarketHubConnection();
    
    res.sendStatus(200); // Send HTTP 200 OK
  } else {
    console.warn('[Bridge][HTTP] Token update failed: Missing token');
    res.status(400).send('Missing token'); // Send HTTP 400 Bad Request
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
