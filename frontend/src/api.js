const base = ''

async function req(path, opts = {}) {
  const r = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await r.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) {
    let msg = (data && (data.detail || data.message)) || r.statusText
    if (msg && typeof msg === 'object' && !Array.isArray(msg)) msg = msg.message || msg.code || JSON.stringify(msg)
    const e = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    e.status = r.status
    e.data = data
    throw e
  }
  return data
}

export const api = {
  dashboard: () => req('/api/dashboard'),
  tills: () => req('/api/tills'),
  lines: (day) => req('/api/lines' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  createLine: (body) => req('/api/lines', { method: 'POST', body: JSON.stringify(body) }),
  closePreview: (day) => req('/api/close/preview' + (day ? `?business_day=${encodeURIComponent(day)}` : '')),
  seal: (body) => req('/api/close/seal', { method: 'POST', body: JSON.stringify(body) }),
  batches: () => req('/api/close/batches'),
  batchesCurrent: () => req('/api/close/batches?current_only=true'),
  variances: () => req('/api/close/variances'),
  variancesCurrent: () => req('/api/close/variances?current_only=true'),
  resealPolicy: () => req('/api/close/policy'),
  setResealPolicy: (policy) => req('/api/settings/reseal-policy', { method: 'PUT', body: JSON.stringify({ policy }) }),
  integrity: () => req('/api/close/integrity'),
  getDay: () => req('/api/settings/business-day'),
  setDay: (business_day) => req('/api/settings/business-day', { method: 'PUT', body: JSON.stringify({ business_day }) }),
}
