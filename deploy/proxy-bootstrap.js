// Patches Node's global HTTP(S) agents to route through the egress proxy.
// Loaded via NODE_OPTIONS=--require before any application code runs.
// Uses https-proxy-agent which implements CONNECT tunneling correctly.

const https = require('https');
const http = require('http');

const httpsProxy = process.env.HTTPS_PROXY || process.env.https_proxy;
const httpProxy = process.env.HTTP_PROXY || process.env.http_proxy;

if (httpsProxy) {
  const { HttpsProxyAgent } = require('/usr/local/lib/node_modules/https-proxy-agent/dist/index.js');
  https.globalAgent = new HttpsProxyAgent(httpsProxy);
}

if (httpProxy) {
  const { HttpProxyAgent } = require('/usr/local/lib/node_modules/http-proxy-agent/dist/index.js');
  http.globalAgent = new HttpProxyAgent(httpProxy);
}
