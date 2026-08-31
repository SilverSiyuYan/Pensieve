// Docker/Nginx proxies /api and health endpoints to the backend, so use same-origin requests.
window.MEMORY_AGENT_CONFIG = Object.freeze({
  apiBase: '',
  timezone: 'Asia/Shanghai',
});
