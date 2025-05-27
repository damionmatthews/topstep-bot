// signalrBridge.cjs
const express = require('express');
const { HubConnectionBuilder, HttpTransportType, LogLevel } = require('@microsoft/signalr');
const axios = require('axios');

const PORT = 10000;
const ACCOUNT_ID = process.env.ACCOUNT_ID; 
const CONTRACT_ID = process.env.CONTRACT_ID; // <--- MAKE SURE THIS IS SET IN RENDER ENV VARS!

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
async function startHubConnection(hubName, hubUrl, connectionRefVarName, connectedFlagRef, subscribeFn) {
  console.log(`[Bridge] Initializing ${hubName} SignalR Bridge...`);

  // --- Ensure previous connection is fully stopped before creating a new one ---
  // Get the actual connection object from the global scope using its name
  let existingConnection = null;
  if (hubName === "User Hub") existingConnection = userHubConnection;
  if (hubName === "Market Hub") existingConnection = marketHubConnection;

  if (existingConnection && existingConnection.state !== 'Disconnected') {
      try {
          console.log(`[Bridge][${hubName}] Stopping existing connection (${existingConnection.state})...`);
          await existingConnection.stop();
          console.log(`[Bridge][${hubName}] Previous connection stopped.`);
      } catch (e) {
          console.warn(`[Bridge][${hubName}] Error stopping previous connection:`, e.message);
      } finally {
          // Clear the global reference after stopping
          if (hubName === "User Hub") userHubConnection = null;
          if (hubName === "Market Hub") marketHubConnection = null;
      }
  }
  
  const connection = new HubConnectionBuilder()
    .withUrl(hubUrl, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => currentTopstepToken,
      timeoutInMilliseconds: 60 * 1000 // 60 seconds handshake timeout
    })
    .configureLogging(LogLevel.Trace)
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
if (hubName === "Market Hub") marketHubConnection = connection;

// --- Add a listener for the 'Connected' event (or 'ServerHandshakeComplete' if it exists) ---
// This listener should only fire *after* the connection is fully ready for invocations.
connection.on('Connected', () => { // This event name might need to be verified or adapted
    console.log(`[Bridge][${hubName}] Connection state is now 'Connected'. Attempting subscriptions...`);
    subscribeFn();
});

  // --- Event Listeners (Must be set BEFORE .start()) ---
  connection.onclose(error => {
    if (hubName === "User Hub") userHubConnected = false;
    if (hubName === "Market Hub") marketHubConnected = false;
    console.error(`[Bridge][${hubName}] Connection closed.`, error || 'No error specified');
  });

  connection.onreconnected(() => {
    console.log(`[Bridge][${hubName}] Reconnected! Re-subscribing...`);
    subscribeFn();
  });

  // Data forwarding listeners
  if (hubName === "User Hub") {
    connection.on("GatewayUserTrade", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserTrade', data: data }));
    connection.on("GatewayUserOrder", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserOrder', data: data }));
    connection.on("GatewayUserPosition", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserPosition', data: data }));
    connection.on("GatewayUserAccount", (data) => sendEventToN8n(N8N_USER_FILL_WEBHOOK_URL, { type: 'GatewayUserAccount', data: data }));
  } else if (hubName === "Market Hub") {
    connection.on("GatewayQuote", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayQuote', data: data }));
    connection.on("GatewayTrade", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayTrade', data: data }));
    connection.on("GatewayDepth", (data) => sendEventToN8n(N8N_MARKET_DATA_WEBHOOK_URL, { type: 'GatewayDepth', data: data }));
  }

  // ... onclose, onreconnected, etc. ...

try {
    console.log(`[Bridge][${hubName}] Starting connection...`);
    await connection.start(); 
    
    if (connection.state === 'Connected') { // Check state after start resolves
        if (hubName === "User Hub") userHubConnected = true;
        if (hubName === "Market Hub") marketHubConnected = true;
        console.log(`[Bridge][${hubName}] ✅ SignalR Connected successfully.`);
        // subscribeFn(); // Don't call here if using connection.on('Connected')
    } else {
         // Handle case where start() resolves but state isn't Connected
         throw new Error(`Connection started but state is not 'Connected': ${connection.state}`);
    }
    
 //  try {
 //    console.log(`[Bridge][${hubName}] Starting connection...`);
 //    await connection.start(); // This resolves AFTER handshake is complete
 //    
  // Set connected flag
 //  if (hubName === "User Hub") userHubConnected = true;
 //  if (hubName === "Market Hub") marketHubConnected = true;
 //  
 //  console.log(`[Bridge][${hubName}] ✅ SignalR Connected successfully.`);
 //  
  // --- IMMEDIATELY INVOKE SUBSCRIPTION, NO TIMEOUT ---
 //  subscribeFn(); 
    
  } catch (err) {
    if (hubName === "User Hub") userHubConnected = false;
    if (hubName === "Market Hub") marketHubConnected = false;
    console.error(`[Bridge][${hubName}] Failed to connect:`, err);
    // Retry connection after a delay
    setTimeout(() => startHubConnection(hubName, hubUrl, connectionRefVarName, connectedFlagRef, subscribeFn), 5000); 
  }
}

// --- Subscription Helper Functions ---
function subscribeToUserHub() {
  if (!userHubConnection || userHubConnection.state !== 'Connected') { // Check connection state explicitly
    console.warn('[Bridge][UserHub] Cannot subscribe, User Hub not in Connected state.');
    return;
  }
  if (!ACCOUNT_ID) {
    console.error('[Bridge][UserHub] ACCOUNT_ID is not defined in environment variables. Cannot subscribe to user data.');
    return;
  }

  console.log(`[Bridge][UserHub] Invoking User Hub subscriptions for Account ID: ${ACCOUNT_ID}...`);
  // Ensure invoke calls are robust against rapid disconnects by checking state again
  userHubConnection.invoke('SubscribeAccounts') 
    .then(() => console.log('[Bridge][UserHub] SubscribeAccounts invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeAccounts error:', err.message)); // Log only message
  userHubConnection.invoke('SubscribeOrders', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribeOrders invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeOrders error:', err.message));
  userHubConnection.invoke('SubscribePositions', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribePositions invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribePositions error:', err.message));
  userHubConnection.invoke('SubscribeTrades', ACCOUNT_ID) 
    .then(() => console.log('[Bridge][UserHub] SubscribeTrades invoked.'))
    .catch(err => console.error('[Bridge][UserHub] SubscribeTrades error:', err.message));
}

function subscribeToMarketHub() {
  if (!marketHubConnection || marketHubConnection.state !== 'Connected') { // Check connection state explicitly
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
        // Using axios.post for HTTP POST request
        const response = await axios.post(webhookUrl, payload, {
            headers: { 'Content-Type': 'application/json' }
        });
        // console.log(`[Bridge][N8N] Event sent to ${webhookUrl}: ${response.status}`); // Too verbose for production logs
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

    // Start both hub connections with the new token
    // These functions now handle stopping previous connections internally
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
