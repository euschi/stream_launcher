// Instrada tutto il traffico del profilo sul proxy configurato, credenziali comprese.
// PROXY_CONFIG arriva da config.js, generato da stream-launcher a ogni avvio.
const cfg = PROXY_CONFIG;
const isSocks = cfg.type === "socks5" || cfg.type === "socks4";

function proxyInfo() {
  const info = {
    type: cfg.type === "socks5" ? "socks" : cfg.type,
    host: cfg.host,
    port: cfg.port,
    failoverTimeout: 5,
  };
  if (isSocks) {
    info.proxyDNS = true;
    if (cfg.username) {
      info.username = cfg.username;
      info.password = cfg.password;
    }
  } else if (cfg.type === "https" && cfg.username) {
    // Accettato da Firefox solo per i proxy HTTPS; per HTTP vale onAuthRequired (sfida 407).
    info.proxyAuthorizationHeader = "Basic " + btoa(`${cfg.username}:${cfg.password}`);
  }
  return info;
}

browser.proxy.onRequest.addListener(proxyInfo, { urls: ["<all_urls>"] });

// Riserva per i proxy HTTP che chiedono l'autenticazione con una sfida 407.
browser.webRequest.onAuthRequired.addListener(
  (details) =>
    details.isProxy && cfg.username
      ? { authCredentials: { username: cfg.username, password: cfg.password } }
      : {},
  { urls: ["<all_urls>"] },
  ["blocking"],
);

browser.proxy.onError.addListener((error) => console.error("Proxy:", error.message));
