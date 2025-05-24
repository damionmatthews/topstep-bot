// signalrBridge.cjs
const { HubConnectionBuilder, HttpTransportType, LogLevel, HubConnectionState } = require('@microsoft/signalr');
const axios = require('axios');
const express = require('express');
const app = express();

// --- CONFIGURATION ---
let TOPSTEPX_SESSION_TOKEN = process.env.TOPSTEP_TOKEN; 

// Define N8N_EVENT_WEBHOOK_URL clearly at the top
const N8N_USER_FILL_WEBHOOK_URL_FROM_ENV = process.env.N8N_USER_FILL_WEBHOOK_URL;
const DEFAULT_N8N_WEBHOOK_URL = "https://dcminsf.app.n8n.cloud/webhook/user-fill-event-bridge";
const N8N_EVENT_WEBHOOK_URL = N8N_USER_FILL_WEBHOOK_URL_FROM_ENV || DEFAULT_N8N_WEBHOOK_URL;

const ACCOUNT_ID = process.env.ACCOUNT_ID;
const BRIDGE_API_SECRET = process.env.BRIDGE_API_SECRET;
const BRIDGE_PORT = process.env.PORT || 3001;

// --- Validate essential configuration ---
if (!TOPSTEPX_SESSION_TOKEN || TOPSTEPX_SESSION_TOKEN === "your-token-here") {
  console.warn("⚠️ WARNING: TOPSTEP_TOKEN environment variable is not set optimally or is a placeholder.");
}

// Now check the N8N_EVENT_WEBHOOK_URL that was just defined
if (!N8N_EVENT_WEBHOOK_URL) { // This is the line that was erroring (around line 18-20 in your file)
  console.error("❌ FATAL: N8N_EVENT_WEBHOOK_URL could not be determined (either from ENV or default).");
  process.exit(1);
}
// The rest of your validations
if (!ACCOUNT_ID) {
    console.error("❌ FATAL: ACCOUNT_ID environment variable is not set. Required for User Hub subscriptions.");
    process.exit(1);
}
if (!BRIDGE_API_SECRET) {
    console.warn("⚠️ WARNING: BRIDGE_API_SECRET is not set. The /update-token endpoint will be unsecured.");
}

const USER_HUB_URL = `https://rtc.topstepx.com/hubs/user`;

let userHubConnection;
let isUserHubConnected = false;

// --- HTTP Server for Token Updates ---
app.use(express.json()); // Middleware to parse JSON request bodies

app.post('/update-token', async (req, res) => {
    const providedSecret = req.headers['x-bridge-secret'];
    if (BRIDGE_API_SECRET && providedSecret !== BRIDGE_API_SECRET) { // Only check secret if it's configured
        console.warn("[Bridge][HTTP] Unauthorized attempt to update token. Invalid secret.");
        return res.status(401).send({ success: false, message: 'Unauthorized: Invalid secret.' });
    }

    const newToken = req.body.token;
    if (newToken && typeof newToken === 'string') {
        console.log("[Bridge][HTTP] Received new token from n8n.");
        TOPSTEPX_SESSION_TOKEN = newToken; // Update the module-level token variable
        
        // Restart SignalR connection to use the new token
        if (userHubConnection) {
            console.log("[Bridge][HTTP] Restarting User Hub connection with new token...");
            // It's important that userHubConnection.stop() completes before startUserHubConnection() is called again.
            try {
                await userHubConnection.stop(); // Ensure it stops before restarting
                console.log("[Bridge][SignalR] Previous User Hub connection stopped for token update.");
            } catch (stopError) {
                console.error("[Bridge][SignalR] Error stopping User Hub connection during token update:", stopError.message || stopError);
            }
            // Ensure the new token is used by accessTokenFactory by re-creating or restarting the connection logic
            await startUserHubConnection(); // Re-initialize and start
        } else {
            // If connection wasn't even up, try starting it now with the new token
            await startUserHubConnection();
        }
        res.status(200).send({ success: true, message: 'Token updated and SignalR connection (re)initiated.' });
    } else {
        console.warn("[Bridge][HTTP] Token missing or invalid in /update-token request body.");
        res.status(400).send({ success: false, message: 'Token missing or invalid in request body.' });
    }
});

app.get('/health', (req, res) => { // Simple health check endpoint
    res.status(200).send({ status: 'UP', userHubConnected: isUserHubConnected, hasToken: !!TOPSTEPX_SESSION_TOKEN });
});

app.listen(BRIDGE_PORT, () => {
    console.log(`[Bridge][HTTP] Server listening on port ${BRIDGE_PORT}. /update-token and /health active.`);
});


// --- SignalR Logic ---
async function postEventToN8n(eventName, eventData) {
    if (!N8N_EVENT_WEBHOOK_URL) {
        console.error(`[Bridge] Cannot POST '${eventName}': N8N_EVENT_WEBHOOK_URL is not set.`);
        return;
    }
    console.log(`[Bridge][SignalR] Attempting to POST '${eventName}' to n8n: ${N8N_EVENT_WEBHOOK_URL}`);
    // console.log(`[Bridge][SignalR] Event Data for ${eventName}:`, JSON.stringify(eventData, null, 2)); 
    
    try {
        const response = await axios.post(N8N_EVENT_WEBHOOK_URL, eventData, {
            headers: { 'Content-Type': 'application/json' },
            timeout: 15000 // 15 second timeout
        });
        console.log(`[Bridge][SignalR] Successfully POSTed '${eventName}' to n8n. n8n status: ${response.status}`);
    } catch (error) {
        console.error(`[Bridge][SignalR] ❌ Error POSTing '${eventName}' to n8n: ${error.message}`);
        if (error.response) {
            console.error("[Bridge][SignalR] n8n Error Response Status:", error.response.status);
            console.error("[Bridge][SignalR] n8n Error Response Data:", JSON.stringify(error.response.data, null, 2));
        } else if (error.request) {
            console.error("[Bridge][SignalR] n8n No response received. Is n8n webhook active and URL correct?");
        }
    }
}

async function startUserHubConnection() {
    if (!TOPSTEPX_SESSION_TOKEN || TOPSTEPX_SESSION_TOKEN === "your-token-here") {
        console.warn("[Bridge][SignalR] Cannot start User Hub connection: TOPSTEP_TOKEN is missing or placeholder.");
        isUserHubConnected = false;
        return;
    }
    if (userHubConnection && (userHubConnection.state === HubConnectionState.Connected || userHubConnection.state === HubConnectionState.Connecting || userHubConnection.state === HubConnectionState.Reconnecting)) {
        console.log("[Bridge][SignalR] User Hub connection attempt skipped: already connected or connecting/reconnecting.");
        return;
    }
    console.log("[Bridge][SignalR] Initializing User Hub connection...");
    userHubConnection = new HubConnectionBuilder()
        .withUrl(USER_HUB_URL, {
            accessTokenFactory: () => TOPSTEPX_SESSION_TOKEN,
            // skipNegotiation: true,
            transport: HttpTransportType.WebSockets
        })
        .withAutomaticReconnect([0, 3000, 10000, 30000, 60000, null]) // Added a 60s delay
        .configureLogging(LogLevel.Information)
        .build();

    userHubConnection.on("GatewayUserTrade", (tradeEvent) => {
        console.log("[Bridge][SignalR] === Received GatewayUserTrade ===");
        postEventToN8n("GatewayUserTrade", tradeEvent);
    });

    userHubConnection.on("GatewayUserOrder", (orderEvent) => {
        console.log("[Bridge][SignalR] === Received GatewayUserOrder ===");
        postEventToN8n("GatewayUserOrder", orderEvent); // Forwarding all user events now
    });

    userHubConnection.on("GatewayUserPosition", (positionEvent) => {
        console.log("[Bridge][SignalR] === Received GatewayUserPosition ===");
        postEventToN8n("GatewayUserPosition", positionEvent);
    });
    
    userHubConnection.on("GatewayUserAccount", (accountEvent) => {
        console.log("[Bridge][SignalR] === Received GatewayUserAccount ===");
        // postEventToN8n("GatewayUserAccount", accountEvent); // Decide if you need this in n8n
    });

    userHubConnection.onclose(async (error) => {
        isUserHubConnected = false;
        console.warn(`[Bridge][SignalR] User Hub connection closed. Error: ${error || "No error specified."}`);
        // withAutomaticReconnect should handle restarting.
    });

    userHubConnection.onreconnecting(async (error) => {
        isUserHubConnected = false;
        console.warn(`[Bridge][SignalR] User Hub attempting to reconnect due to: ${error || "No error specified."}`);
    });

    userHubConnection.onreconnected(async (connectionId) => {
        isUserHubConnected = true;
        console.info(`[Bridge][SignalR] User Hub reconnected with ID: ${connectionId}. Re-invoking subscriptions.`);
        await invokeUserHubSubscriptions();
    });

    try {
        await userHubConnection.start();
        isUserHubConnected = true;
        console.log("[Bridge][SignalR] ✅ User Hub SignalR Connected successfully.");
        await invokeUserHubSubscriptions();
    } catch (err) {
        isUserHubConnected = false;
        console.error("[Bridge][SignalR] ❌ User Hub SignalR Initial Connection Failed:", err.message || err);
        // The automatic reconnect might still kick in.
    }
}

async function invokeUserHubSubscriptions() {
    if (userHubConnection && userHubConnection.state === HubConnectionState.Connected) {
        try {
            const accountIdNum = parseInt(ACCOUNT_ID);
            if (isNaN(accountIdNum)) { /* ... */ return; }
            console.log(`[Bridge][SignalR] Invoking User Hub subscriptions for Account ID: ${accountIdNum}...`);
            
            await userHubConnection.invoke("SubscribeAccounts").catch(err => console.error("[Bridge][SignalR] SubscribeAccounts error:", err.toString()));
            console.log("[Bridge][SignalR] SubscribeAccounts invoked.");

            // Temporarily comment out others:
            // await userHubConnection.invoke("SubscribeOrders", accountIdNum).catch(err => console.error("[Bridge][SignalR] SubscribeOrders error:", err.toString()));
            // await userHubConnection.invoke("SubscribePositions", accountIdNum).catch(err => console.error("[Bridge][SignalR] SubscribePositions error:", err.toString()));
            // await userHubConnection.invoke("SubscribeTrades", accountIdNum).catch(err => console.error("[Bridge][SignalR] SubscribeTrades error:", err.toString()));
            
            console.log("[Bridge][SignalR] ✅ User Hub subscriptions (partially) invoked.");
        } catch (err) {
            console.error("[Bridge][SignalR] ❌ Error invoking User Hub subscriptions:", err.message || err);
        }
    } else {
        console.warn("[Bridge][SignalR] Cannot invoke subscriptions: User Hub not connected or not in correct state.");
    }
}

// --- Graceful Shutdown ---
async function shutdown() {
    console.log("[Bridge] Initiating shutdown sequence...");
    if (userHubConnection) {
        console.log("[Bridge] Stopping User Hub connection...");
        await userHubConnection.stop().catch(err => console.error("[Bridge] Error stopping User Hub connection during shutdown:", err));
        console.log("[Bridge] User Hub connection stopped.");
    }
    // Give a moment for async operations if any, then exit
    setTimeout(() => {
        console.log("[Bridge] Exiting.");
        process.exit(0);
    }, 1000);
}
process.on('SIGINT', shutdown); // CTRL+C
process.on('SIGTERM', shutdown); // kill command (Render uses SIGTERM)

// --- Start the SignalR connection attempt ---
console.log("[Bridge] Initializing TopstepX SignalR Bridge...");
// Don't start immediately if token is placeholder, wait for /update-token call
if (TOPSTEPX_SESSION_TOKEN && TOPSTEPX_SESSION_TOKEN !== "your-token-here") {
    startUserHubConnection();
} else {
    console.warn("[Bridge] Waiting for token update via /update-token endpoint to start User Hub connection.");
}

// Note: Market Hub connection logic would be similar but use a different Hub URL and event handlers/n8n webhooks.
