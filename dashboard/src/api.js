const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').trim();

function getToken() {
  return localStorage.getItem('att_token');
}

export function setToken(token) {
  if (token) {
    localStorage.setItem('att_token', token);
  } else {
    localStorage.removeItem('att_token');
  }
}

export function getStoredUser() {
  try {
    const raw = localStorage.getItem('att_user');
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(user) {
  if (user) {
    localStorage.setItem('att_user', JSON.stringify(user));
  } else {
    localStorage.removeItem('att_user');
  }
}

export function clearAuth() {
  setToken(null);
  setStoredUser(null);
}

/**
 * Core fetch wrapper. Adds Authorization header, handles 401 by clearing auth.
 * companyCtx: optional company_id override for platform_admin context switching.
 */
export async function api(path, options = {}, companyCtx = null) {
  const token = getToken();
  const isFormData = options.body instanceof FormData;

  const headers = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers || {}),
  };

  let url = `${API_BASE}${path}`;
  if (companyCtx) {
    const sep = url.includes('?') ? '&' : '?';
    url += `${sep}company_ctx=${companyCtx}`;
  }

  const res = await fetch(url, { ...options, headers });

  if (res.status === 401) {
    clearAuth();
    window.dispatchEvent(new Event('auth:expired'));
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }

  return res.json();
}

export { API_BASE };
