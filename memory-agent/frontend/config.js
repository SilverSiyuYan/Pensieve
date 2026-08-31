// Windows/local static-server defaults. Deployments may replace this file.
window.MEMORY_AGENT_CONFIG = Object.freeze({
  // Local development default: the FastAPI backend listens on 8001, while the frontend is served on 8080.
  // ?apiBase=... still takes precedence in api-runtime.js.
  apiBase: '',
  // Must match backend APP_TIMEZONE; the health response remains authoritative.
  timezone: 'Asia/Shanghai',
});
