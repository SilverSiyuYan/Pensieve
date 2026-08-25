(function (global) {
  class ApiError extends Error {
    constructor(kind, message, status = 0) {
      super(message);
      this.name = 'ApiError';
      this.kind = kind;
      this.status = status;
    }
  }

  function normaliseApiBase(rawValue, pageHref) {
    const page = new URL(pageHref);
    const value = String(rawValue || '').trim();
    if (!value) {
      if (!['http:', 'https:'].includes(page.protocol)) throw new ApiError('config', '页面不能直接从文件打开，请通过项目前端地址访问。');
      return page.origin;
    }
    let parsed;
    try { parsed = new URL(value); } catch { throw new ApiError('config', `API 地址无效：${value}`); }
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new ApiError('config', 'API 地址只允许使用 http 或 https。');
    if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new ApiError('config', 'API 地址不能包含凭据、查询参数或片段。');
    if (!/^\/*$/.test(parsed.pathname) && !/^\/api\/*$/.test(parsed.pathname)) throw new ApiError('config', 'API 地址只能填写服务根地址；末尾的 /api 会自动移除。');
    if (page.protocol === 'https:' && parsed.protocol === 'http:') throw new ApiError('config', 'HTTPS 页面不能连接 HTTP API，请使用 HTTPS 后端地址。');
    return parsed.origin;
  }

  function resolveApiBase(pageHref, projectApiBase) {
    const page = new URL(pageHref);
    const queryApiBase = page.searchParams.get('apiBase');
    return normaliseApiBase(queryApiBase !== null ? queryApiBase : projectApiBase, pageHref);
  }

  function statusError(status, detail = '') {
    const suffix = detail ? `：${detail}` : '';
    if (status === 401) return new ApiError('auth', `登录失效或尚未登录${suffix}`, 401);
    if (status === 403) return new ApiError('forbidden', `无权限执行此操作${suffix}`, 403);
    if (status === 404) return new ApiError('not-found', `接口不存在，可能是前后端版本不匹配${suffix}`, 404);
    if (status === 504) return new ApiError('upstream-timeout', `模型服务响应超时${suffix}`, 504);
    if (status >= 500) return new ApiError('server', `后端内部错误（HTTP ${status}）${suffix}`, status);
    return new ApiError('http', detail || `请求失败（HTTP ${status}）`, status);
  }

  function transportError(error, apiBase, pageOrigin, timeoutMs) {
    if (error && error.name === 'AbortError') return new ApiError('timeout', `请求超时（${timeoutMs / 1000} 秒）。`);
    const errorCode = error && (error.code || error.cause?.code);
    if (errorCode === 'ECONNREFUSED') {
      return new ApiError('network-refused', `后端拒绝连接（${apiBase}）。请确认服务已经启动并监听该地址。`);
    }
    return new ApiError(
      'browser-network',
      `无法连接后端服务或读取其响应（${apiBase}）。请确认服务已启动，并在开发者工具 Network/Console 中检查网络拒绝、CORS 或浏览器安全策略；页面来源为 ${pageOrigin}。`,
    );
  }

  function validateHealthResponse(health) {
    if (!health || health.application !== 'memory-agent' || typeof health.version !== 'string') {
      throw new ApiError('version', '健康检查响应不是 Pensieve memory-agent，API 地址可能指向了其他服务。');
    }
    if (health.status !== 'ok' || health.database_accessible !== true) {
      throw new ApiError('database', '后端已连接，但数据库当前不可访问。');
    }
    if (typeof health.timezone !== 'string' || !health.timezone) {
      throw new ApiError('version', '健康检查缺少项目时区，前后端版本可能不匹配。');
    }
    return health;
  }

  async function parseJsonResponse(response) {
    let data;
    try { data = await response.json(); }
    catch {
      if (!response.ok) throw statusError(response.status);
      throw new ApiError('response-format', `后端响应格式异常：期望 JSON，实际为 ${response.headers.get('content-type') || '未知类型'}。`, response.status);
    }
    if (!response.ok) throw statusError(response.status, data.detail || '');
    return data;
  }

  global.MemoryAgentApi = Object.freeze({ ApiError, normaliseApiBase, resolveApiBase, statusError, transportError, parseJsonResponse, validateHealthResponse });
})(globalThis);
