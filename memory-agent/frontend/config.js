// Windows/local static-server defaults. Deployments may replace this file.
window.MEMORY_AGENT_CONFIG = Object.freeze({
  // ?apiBase=... still takes precedence in api-runtime.js.
  apiBase: '',
  // Must match backend APP_TIMEZONE; the health response remains authoritative.
  timezone: 'Asia/Shanghai',
});
