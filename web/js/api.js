// api.js -- the only place the browser talks to the Python backend.
// Every routing result and every chart number arrives through these calls.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function getJSON(url, signal) {
  let res;
  try {
    res = await fetch(url, { signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError('Cannot reach the server. Is `python server.py` still running?', 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* non-JSON error page */
  }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : res.statusText;
    throw new ApiError(msg || `Request failed (${res.status})`, res.status);
  }
  return data;
}

const q = (params) => new URLSearchParams(params).toString();

export const api = {
  city: () => getJSON('/api/city'),
  route: (p, signal) =>
    getJSON(`/api/route?${q({ origin: p.origin, dest: p.dest, algo: p.algo, penalty: p.penalty, replay: p.replay ? 1 : 0 })}`, signal),
  sweep: (origin, dest, signal) => getJSON(`/api/sweep?${q({ origin, dest })}`, signal),
  flips: () => getJSON('/api/flips?limit=10'),
  experiments: () => getJSON('/api/experiments'),
};
