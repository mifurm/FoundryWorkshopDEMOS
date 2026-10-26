'use strict';

const crypto = require('node:crypto');
const SESSION = 'Foundry Browser Lab';
const FIELDS = { name: 'Workshop Bot', email: 'bot@example.invalid' };
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(Object.fromEntries(
  Object.keys(value).sort().map(key => [key, value[key]])));
const digest = value => hash(canonical(value));
const same = (a, b) => canonical(a) === canonical(b);
const escape = value => String(value).replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function action(base, runId, formId) {
  return {
    site: base, method: 'POST', path: `/api/runbook/${runId}/submit`,
    runId, formId, session: SESSION, ...FIELDS
  };
}

function html(state, path, message = '') {
  const root = `/api/runbook/${state.action.runId}`;
  const values = state.draft || { name: '', email: '' };
  const header = '<!doctype html><meta charset="utf-8"><title>Synthetic workshop</title>' +
    '<h1>Workshop registration — SYNTHETIC ONLY</h1>';
  if (path === 'site') {
    return header + `<a id="session" href="${root}/form">${SESSION}</a>`;
  }
  if (state.record) {
    return header + `<h2>Registration recorded</h2><p>Record: ${state.record.id}</p>` +
      `<p>${escape(canonical(state.record.action))}</p>`;
  }
  return header + `<h2>${SESSION}</h2><p>Form: ${state.action.formId}</p>` +
    `<p role="status">${escape(message)}</p>` +
    `<form method="post" action="${root}/submit">` +
    `<input type="hidden" name="formId" value="${state.action.formId}">` +
    `<label>Name <input id="name" name="name" value="${escape(values.name)}"></label>` +
    `<label>Email <input id="email" name="email" value="${escape(values.email)}"></label>` +
    `<button id="save-draft" type="submit" formaction="${root}/draft">Save draft</button>` +
    '<button id="submit" type="submit">Submit synthetic registration</button></form>';
}

// The only persistent state is a per-run blob. ETags serialize draft/permit/record writes.
class BlobStore {
  async request(id, method, body, etag) {
    const identity = new URL(process.env.IDENTITY_ENDPOINT);
    identity.searchParams.set('api-version', '2019-08-01');
    identity.searchParams.set('resource', 'https://storage.azure.com/');
    const auth = await fetch(identity, {
      headers: { 'X-IDENTITY-HEADER': process.env.IDENTITY_HEADER }
    });
    if (!auth.ok) throw new Error('Managed identity token unavailable');
    const token = (await auth.json()).access_token;
    const headers = {
      Authorization: `Bearer ${token}`, 'x-ms-version': '2023-11-03',
      'x-ms-date': new Date().toUTCString()
    };
    if (method === 'PUT') {
      headers['x-ms-blob-type'] = 'BlockBlob';
      headers['Content-Type'] = 'application/json';
      headers[etag ? 'If-Match' : 'If-None-Match'] = etag || '*';
    }
    const result = await fetch(
      `https://${process.env.RUN_STORAGE}.blob.core.windows.net/runs/${id}.json`,
      { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (result.status === 404 && method !== 'PUT') return null;
    if (!result.ok) throw new Error(`Blob operation failed: ${result.status}`);
    return method === 'GET'
      ? { state: await result.json(), etag: result.headers.get('etag') } : true;
  }
  get(id) { return this.request(id, 'GET'); }
  put(id, body, etag) { return this.request(id, 'PUT', body, etag); }
  delete(id) { return this.request(id, 'DELETE'); }
}

async function handle(req, store, base, now = Date.now()) {
  const path = req.params.rest || '';
  if (path === 'health' && req.method === 'GET') {
    return { status: 200, body: { fixture: 'browser-runbook-v1', syntheticOnly: true, site: base } };
  }
  const match = /^([a-f0-9]{32})\/(create|state|site|form|draft|approve|submit|delete)$/.exec(path);
  if (!match) return { status: 404 };
  const [, id, operation] = match;
  if ((operation === 'create' && req.method !== 'PUT') ||
      (['state', 'site', 'form'].includes(operation) && req.method !== 'GET') ||
      (['draft', 'approve', 'submit'].includes(operation) && req.method !== 'POST') ||
      (operation === 'delete' && req.method !== 'DELETE')) return { status: 405 };
  const body = typeof req.body === 'string'
    ? Object.fromEntries(new URLSearchParams(req.body)) : (req.body || {});
  if (operation === 'create') {
    if (!/^[a-f0-9]{64}$/.test(body.controlHash) || !/^[a-f0-9]{32}$/.test(body.formId)) {
      return { status: 400 };
    }
    const state = {
      action: action(base, id, body.formId), controlHash: body.controlHash,
      expires: now + 30 * 60 * 1000, draft: null, permit: null, record: null, rejected: 0
    };
    await store.put(id, state);
    return { status: 201, body: { action: state.action, expires: state.expires } };
  }
  const loaded = await store.get(id);
  if (!loaded) return { status: 404 };
  const { state, etag } = loaded;
  const control = hash(req.headers['x-run-control'] || '') === state.controlHash;
  if (['state', 'approve', 'delete'].includes(operation) && !control) return { status: 403 };
  if (operation === 'delete') {
    await store.delete(id);
    return { status: 204 };
  }
  if (now >= state.expires) return { status: 410 };
  if (operation === 'state') {
    const { controlHash, ...publicState } = state;
    return { status: 200, body: publicState };
  }
  const page = message => ({
    status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' },
    body: html(state, operation, message)
  });
  if (['site', 'form'].includes(operation)) return page('');
  const expected = { ...FIELDS, formId: state.action.formId };
  if (operation === 'draft') {
    if (state.permit || state.record || !same(body, expected)) return { status: 409 };
    state.draft = body;
    await store.put(id, state, etag);
    return page('Draft saved — not submitted');
  }
  if (operation === 'approve') {
    if (body.digest !== digest(state.action) || !same(state.draft || {}, expected) ||
        state.record || state.permit) return { status: 409 };
    state.permit = { digest: body.digest, expires: Math.min(state.expires, now + 120000) };
    await store.put(id, state, etag);
    return { status: 200, body: { approved: body.digest } };
  }
  if (operation === 'submit') {
    if (!state.permit || now >= state.permit.expires || state.record ||
        !same(body, expected) || !same(body, state.draft || {}) ||
        state.permit.digest !== digest(state.action)) {
      state.rejected++;
      await store.put(id, state, etag);
      return { ...page('Approval required or action mismatch — no registration created'), status: 403 };
    }
    state.record = { id: crypto.randomUUID(), action: state.action };
    state.permit = null;
    await store.put(id, state, etag);
    return page('');
  }
  return { status: 404 };
}

module.exports = async function (context, req) {
  try {
    context.res = await handle(req, new BlobStore(), process.env.SITE_BASE);
  } catch {
    // Never echo tokens, request bodies, browser URLs or storage errors.
    context.res = { status: 503, body: { error: 'Fixture unavailable; inspect state before any retry.' } };
  }
  context.res.headers = {
    ...context.res.headers, 'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'
  };
};
module.exports.handle = handle;
module.exports.digest = digest;
