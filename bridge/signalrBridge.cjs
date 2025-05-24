// signalrBridge.cjs
const express = require('express');
const { HubConnectionBuilder, HttpTransportType, LogLevel } = require('@microsoft/signalr');
const axios = require('axios');

const PORT = 10000;
const ACCOUNT_ID = process.env.ACCOUNT_ID;
const TOKEN = process.env.ACCESS_TOKEN;
const USER_HUB_URL = `https://rtc.topstepx.com/hubs/user`;

let connection;
let connected = false;

async function startSignalRConnection() {
  console.log('[Bridge] Initializing TopstepX SignalR Bridge...');

  connection = new HubConnectionBuilder()
    .withUrl(USER_HUB_URL, {
      skipNegotiation: true,
      transport: HttpTransportType.WebSockets,
      accessTokenFactory: () => TOKEN,
    })
    .configureLogging(LogLevel.Debug)
    .withAutomaticReconnect({
      nextRetryDelayInMilliseconds: retryContext => {
        const delay = Math.min(10000, retryContext.previousRetryCount * 2000);
        console.log(`[Bridge][SignalR] Retry in ${delay}ms`);
        return delay;
      }
    })
    .build();

  connection.onclose(error => {
    connected = false;
    console.error('[Bridge][SignalR] Connection closed.', error || 'No error specified');
  });

  connection.onreconnected(() => {
    console.log('[Bridge][SignalR] Reconnected! Re-subscribing...');
    subscribeToUserHub();
  });

  try {
    console.log('[Bridge][SignalR] Starting connection...');
    await connection.start();
    connected = true;
    console.log('[Bridge][SignalR] ✅ User Hub SignalR Connected successfully.');
    subscribeToUserHub();
  } catch (err) {
    connected = false;
    console.error('[Bridge][SignalR] Failed to connect:', err);
    setTimeout(startSignalRConnection, 5000);
  }
}

function subscribeToUserHub() {
  if (!connected) {
    console.warn('[Bridge][SignalR] Cannot subscribe, not connected.');
    return;
  }

  console.log(`[Bridge][SignalR] Invoking User Hub subscriptions for Account ID: ${ACCOUNT_ID}...`);

  connection.invoke('SubscribeAccounts', ACCOUNT_ID)
    .then(() => console.log('[Bridge][SignalR] SubscribeAccounts invoked.'))
    .catch(err => console.error('[Bridge][SignalR] SubscribeAccounts error:', err));

  connection.invoke('SubscribeTrades', ACCOUNT_ID)
    .then(() => console.log('[Bridge][SignalR] SubscribeTrades invoked.'))
    .catch(err => console.error('[Bridge][SignalR] SubscribeTrades invoke error:', err));
}

// Express app for token updates and health checks
const app = express();
app.use(express.json());

app.post('/update-token', (req, res) => {
  const { access_token } = req.body;
  if (access_token) {
    console.log('[Bridge][HTTP] Token updated.');
    TOKEN = access_token;
    if (!connected) {
      startSignalRConnection();
    }
    res.sendStatus(200);
  } else {
    res.status(400).send('Missing token');
  }
});

app.get('/health', (req, res) => {
  res.send('OK');
});

app.listen(PORT, () => {
  console.log(`[Bridge][HTTP] Server listening on port ${PORT}. /update-token and /health active.`);
  startSignalRConnection();
});
