// signalrBridge.cjs
const express = require('express');
const { HubConnectionBuilder, HttpTransportType, LogLevel } = require('@microsoft/signalr');
const axios = require('axios'); // Used for sending events to n8n webhooks

const PORT = 10000;
const ACCOUNT_ID = process.env.ACCOUNT_ID; 
const CONTRACT_ID = process.env.CONTRACT_ID; 

const USER_HUB_URL = `https://rtc.topstepx.com/hubs/user`;
const MARKET_HUB_URL = `https://rtc.topstepx.com/hubs/market`;

const N8N_USER_FILL_WEBHOOK_URL = process.env.N8N_USER_FILL_WEBHOOK_URL;
const N8N_MARKET_DATA_WEBHOOK_URL = process.env.N8N_MARKET_DATA_WEBHOOK_URL;

let currentTopstepToken = null;
let userHubConnection = null;
let marketHubConnection = null;

let userHubConnected = false;
let marketHubConnected = false;

// --- Helper to start a hub connection ---
async function startHubConnection(hubName, hubUrl, connectedFlagRef, subscribeFn) {
  console.log(`[Bridge] Initializing ${hubName} SignalR Bridge...`);

  let connection;
  // Get the actual connection object from the global scope using its name
  if (hubName === "User Hub") connection = userHubConnection;
  else if (hubName === "Market Hub") connection = marketHubConnection;

  // Ensure previous connection is fully stopped before creating a new one
  if (connection && connection.state !== 'Disconnected') {
      try {
          console.log(`[Bridge][${hubName}] Stopping existing connection (${connection.state})...`);
          await connection.stop();
          console.log(`[Bridge][${hubName}] Previous connection stopped.`);
      } catch (e) {
          console.warn(`[Bridge][${hubName}] Error stopping previous connection:`, e.message);
      } finally {
          // Clear the global reference after stopping
          if (hubName === "User Hub") userHubConnection = null;
          else if (hubName === "Market Hub") marketHubConnection = null;
      }
  }
  
  connection = new HubConnectionBuilder()
    .withUrl(hubUrl, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => currentTopstepToken,
      timeoutInMilliseconds: 60 * 1000 // 60 seconds handshake timeout
    })
    .configureLogging(LogLevel.Trace) // Increased logging to Trace for more detail
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(30000, retryContext.previousRetryCount * 2000);
        console.log(`[Bridge][${hubName}] Attempting reconnect in ${delay}ms (attempt ${retryContext.previousRetryCount + 1})`);
        return delay;
      }
    })
    .build();

  // Assign connection object to the global reference NOW
  if (hubName === "User Hub") userHubConnection = connection;
  else if (hubName === "Market Hub") marketHubConnection = connection;

  // --- Event Listeners (Must be set BEFORE .start()) ---
  connection.onclose(error => {
    if (hubName === "User Hub") userHubConnected = false;
    else if (hubName === "Market Hub") marketHubConnected = false;
    console.error(`[Bridge][${hubName}] Connection closed.`, error || 'No error specified');
  });

  connection.onreconnected(() => {
    console.log(`[Bridge][${hubName}] Reconnected! Re-subscribing...`);
    subscribeFn();
  });

  // Data forwarding listeners (User Hub)
  if (hubName === "User Hub") {
    connection.on("GatewayUserTrade", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserTrade', data: data }));
    connection.on("GatewayUserOrder", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserOrder', data: data }));
    connection.on("GatewayUserPosition", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserPosition', data: data }));
    connection.on("GatewayUserAccount", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserAccount', data: data }));
  } 
  // Data forwarding listeners (Market Hub)
  else if (hubName === "Market Hub") {
    connection.on("GatewayQuote", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayQuote', data: data }));
    connection.on("GatewayTrade", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayTrade', data: data }));
    connection.on("GatewayDepth", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayDepth', data: data }));
  }

  try {
    console.log(`[Bridge][${hubName}] Starting connection...`);
    await connection.start(); 
    
    // Set connected flag
    if (hubName === "User Hub") userHubConnected = true;
    else if (hubName === "Market Hub") marketHubConnected = true;
    
    console.log(`[Bridge][${hubName}] ✅ SignalR Connected successfully.`);
    
    // --- Call subscription function immediately after start() resolves ---
    subscribeFn(); 
    
  } catch (err) {
    if (hubName === "User Hub") userHubConnected = false;
    else if (hubName === "Market Hub") marketHubConnected = false;
    console.error(`[Bridge][${hubName}] Failed to connect:`, err);
    setTimeout(() => startHubConnection(hubName, hubUrl, connectionRefVarName, connectedFlagRef, subscribeFn), 5000); 
  }
}

// --- Subscription Helper Functions ---
function subscribeToUserHub() {
  if (!userHubConnection || userHubConnection.state !== 'Connected') { 
    console.warn('[Bridge][UserHub] Cannot subscribe, User Hub not in Connected state.');
    return;
  }
  if (!ACCOUNT_ID) {
    console.error('[Bridge][UserHub] ACCOUNT_ID is not defined in environment variables. Cannot subscribe to user data.');
    return;
  }

  console.log(`[Bridge][UserHub] Invoking User Hub subscriptions for Account ID: ${ACCOUNT_ID}...`);
  
  // ProjectX docs mention 'SubscribeAccounts' without parameters for global accounts
  // However, given the immediate disconnect, it might be safer to focus on specific, known subscriptions first.
  // We will try subscribing to account-specific events first, which definitely use ACCOUNT_ID.
  // If SubscribeAccounts() without ID is problematic, we remove it.

  // NOTE: Your previous log shows SubscribeAccounts error: Invocation canceled...
  // This indicates SubscribeAccounts() might be the culprit.
  // We will TRY calling it with ACCOUNT_ID if that makes sense, or commenting it out.
  // Based on ProjectX docs, "SubscribeAccounts" has no parameter, but "SubscribeOrders" does.
  // Let's REMOVE the global SubscribeAccounts() for now, as it's causing issues.
  // Then we will rely on specific account subscriptions.

  userHubConnection.invoke('SubscribeOrders', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribeOrders invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeOrders error:', err.message));
  userHubConnection.invoke('SubscribePositions', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribePositions invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribePositions error:', err.message));
  userHubConnection.invoke('SubscribeTrades', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribeTrades invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeTrades error:', err.message));

  // If you later find you need global account updates, try re-adding:
  // userHubConnection.invoke('SubscribeAccounts')
  //   .then(() => console.log('[Bridge][UserHub] SubscribeAccounts invoked.'))
  //   .catch(err => console.error('[Bridge][UserHub] SubscribeAccounts error:', err.message));
}

function subscribeToMarketHub() {
  if (!marketHubConnection || marketHubConnection.state !== 'Connected') { 
    console.warn('[Bridge][MarketHub] Cannot subscribe, Market Hub not in Connected state.');
    return;
  }
  if (!CONTRACT_ID) {
    console.error('[Bridge][MarketHub] CONTRACT_ID is not defined in environment variables. Cannot subscribe to market data.');
    return;
  }

  console.log(`[Bridge][MarketHub] Invoking Market Hub subscriptions for Contract ID: ${CONTRACT_ID}...`);
  marketHubConnection.invoke('SubscribeContractQuotes', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractQuotes invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractQuotes error:', err.message));
  marketHubConnection.invoke('SubscribeContractTrades', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractTrades invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractTrades error:', err.message));
  marketHubConnection.invoke('SubscribeContractMarketDepth', [CONTRACT_ID])
    .then(() => console.log('[Bridge][MarketHub] SubscribeContractMarketDepth invoked.'))
    .catch(err => console.error('[Bridge][MarketHub] SubscribeContractMarketDepth error:', err.message));
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
        if (!response.ok) { 
             console.error(`[Bridge][N8N] Failed to send event to ${webhookUrl}: ${response.status} ${response.statusText} - Response: ${JSON.stringify(response.data)}`);
        } else {
             // console.log(`[Bridge][N8N] Event sent to ${webhookUrl}: ${response.status}`); // Too verbose for production logs
        }
    } catch (error) {
        console.error(`[Bridge][N8N] Error sending event to ${webhookUrl}:`, error.message);
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
  const { access_token } = req.body; 
  if (access_token) {
    console.log('[Bridge][HTTP] Token updated via /update-token');
    currentTopstepToken = access_token;

    // Call shared connection function for both hubs
    // Pass the name of the global variable holding the connection reference
    startHubConnection("User Hub", USER_HUB_URL, "userHubConnection", userHubConnected, subscribeToUserHub);
    startHubConnection("Market Hub", MARKET_HUB_URL, "marketHubConnection", marketHubConnected, subscribeToMarketHub);
    
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
  console.log('[Bridge][HTTP] Awaiting token via POST /update-token before starting SignalR...');
});
