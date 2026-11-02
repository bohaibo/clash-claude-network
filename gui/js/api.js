let token = '';

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export class SessionError extends ApiError {
  constructor(message = '界面会话已失效，请重新运行「一键网络.cmd」。') {
    super(message, 403);
    this.name = 'SessionError';
  }
}

async function parse(response) {
  const text = await response.text();
  let value = null;
  try { value = text ? JSON.parse(text) : null; } catch { value = null; }
  if (!response.ok) {
    if (response.status === 403) throw new SessionError(value?.error || undefined);
    throw new ApiError(value?.error || ('请求失败（HTTP ' + response.status + '）。'), response.status);
  }
  return value;
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 30000);
  const headers = { 'Cache-Control': 'no-store', ...(options.headers || {}) };
  if (token) headers['X-CCN-Token'] = token;
  try {
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
    return await parse(response);
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('本地界面服务响应超时。');
    if (error instanceof ApiError) throw error;
    throw new ApiError('无法连接本地界面服务。请重新运行「一键网络.cmd」。');
  } finally {
    clearTimeout(timeout);
  }
}

export async function bootstrap() {
  const value = await request('/api/session', { timeout: 10000 });
  token = value.token;
  return value;
}

export function getState(refresh = false) {
  return request('/api/state' + (refresh ? '?refresh=1' : ''), { timeout: 20000 });
}

export function getNodes(refresh = false) {
  return request('/api/nodes' + (refresh ? '?refresh=1' : ''), { timeout: 70000 });
}

export function startJob(payload) {
  return request('/api/job', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeout: 20000,
  });
}

export function getJob(id) {
  return request('/api/job/' + encodeURIComponent(id), { timeout: 15000 });
}

export function cancelJob(id) {
  return request('/api/job-cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
    timeout: 30000,
  });
}

export function getLog(name) {
  return request('/api/log?name=' + encodeURIComponent(name), { timeout: 20000 });
}

export function post(path, payload = {}) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    timeout: path === '/api/update-settings' ? 120000 : 30000,
  });
}

export function heartbeat() {
  return request('/api/heartbeat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
    timeout: 10000,
  });
}
