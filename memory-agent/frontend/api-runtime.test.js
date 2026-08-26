'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
require('./api-runtime.js');

const { normaliseApiBase, resolveApiBase, statusError, transportError, parseJsonResponse, validateHealthResponse } = globalThis.MemoryAgentApi;

const configSandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'config.js'), 'utf8'), configSandbox);
assert.equal(configSandbox.window.MEMORY_AGENT_CONFIG.apiBase, 'http://127.0.0.1:8001');

assert.equal(resolveApiBase('http://127.0.0.1:8080/', 'http://localhost:8001'), 'http://localhost:8001');
assert.equal(resolveApiBase('http://127.0.0.1:8080/?apiBase=http://127.0.0.1:9000/api/', 'http://localhost:8001'), 'http://127.0.0.1:9000');
assert.equal(resolveApiBase('http://127.0.0.1:8080/', ''), 'http://127.0.0.1:8080');
assert.equal(normaliseApiBase('http://localhost:8001////', 'http://localhost:8080/'), 'http://localhost:8001');
assert.throws(() => normaliseApiBase('ftp://localhost:8001', 'http://localhost:8080/'), /http/);
assert.throws(() => normaliseApiBase('http://localhost:8001/wrong', 'http://localhost:8080/'), /根地址/);
assert.throws(() => normaliseApiBase('', 'file:///index.html'), /不能直接从文件打开/);

for (const [status, kind] of [[401, 'auth'], [403, 'forbidden'], [404, 'not-found'], [500, 'server'], [504, 'upstream-timeout']]) {
  const error = statusError(status);
  assert.equal(error.kind, kind);
  assert.equal(error.status, status);
  assert.doesNotMatch(error.message, /无法连接后端/);
}
const refused = transportError({ cause: { code: 'ECONNREFUSED' } }, 'http://127.0.0.1:8001', 'http://127.0.0.1:8080', 2500);
assert.equal(refused.kind, 'network-refused');
assert.match(refused.message, /拒绝连接/);
const browserUnreadable = transportError(new TypeError('Failed to fetch'), 'http://127.0.0.1:8001', 'http://127.0.0.1:8080', 2500);
assert.equal(browserUnreadable.kind, 'browser-network');
assert.match(browserUnreadable.message, /CORS 或浏览器安全策略/);
assert.doesNotMatch(browserUnreadable.message, /浏览器阻止了跨域响应/);
const timeout = transportError({ name: 'AbortError' }, 'http://127.0.0.1:8001', 'http://127.0.0.1:8080', 2500);
assert.equal(timeout.kind, 'timeout');
assert.match(timeout.message, /请求超时/);

assert.equal(validateHealthResponse({ application: 'memory-agent', version: '0.2.0', status: 'ok', database_accessible: true, timezone: 'Asia/Shanghai' }).timezone, 'Asia/Shanghai');
assert.throws(() => validateHealthResponse({ application: 'other-service', version: '9.0', status: 'ok', database_accessible: true }), (error) => error.kind === 'version');
assert.throws(() => validateHealthResponse({ application: 'memory-agent', status: 'ok', database_accessible: true }), (error) => error.kind === 'version');
assert.throws(() => validateHealthResponse({ application: 'memory-agent', version: '0.2.0', status: 'degraded', database_accessible: false, timezone: 'Asia/Shanghai' }), (error) => error.kind === 'database');

(async () => {
  const nonJson = { ok: true, status: 200, headers: { get: () => 'text/html' }, json: async () => { throw new SyntaxError('bad json'); } };
  await assert.rejects(() => parseJsonResponse(nonJson), (error) => error.kind === 'response-format' && !/无法连接后端/.test(error.message));
  const unauthorized = { ok: false, status: 401, headers: { get: () => 'application/json' }, json: async () => ({ detail: 'expired' }) };
  await assert.rejects(() => parseJsonResponse(unauthorized), (error) => error.kind === 'auth' && !/无法连接后端/.test(error.message));
  const forbidden = { ok: false, status: 403, headers: { get: () => 'application/json' }, json: async () => ({ detail: 'denied' }) };
  await assert.rejects(() => parseJsonResponse(forbidden), (error) => error.kind === 'forbidden');
  const missing = { ok: false, status: 404, headers: { get: () => 'application/json' }, json: async () => ({ detail: 'missing' }) };
  await assert.rejects(() => parseJsonResponse(missing), (error) => error.kind === 'not-found');
  const serverError = { ok: false, status: 500, headers: { get: () => 'application/json' }, json: async () => ({ detail: 'boom' }) };
  await assert.rejects(() => parseJsonResponse(serverError), (error) => error.kind === 'server');
  assert.equal(new Set([refused.kind, browserUnreadable.kind, timeout.kind, 'auth', 'forbidden', 'not-found', 'server', 'response-format', 'version']).size, 9);
  console.log('frontend API runtime smoke tests passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
