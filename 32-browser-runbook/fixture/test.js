'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { handle, digest } = require('./index');
const BASE = 'https://fixture.azurewebsites.net/api/runbook';
const ID = '1'.repeat(32), FORM = '2'.repeat(32), CONTROL = '3'.repeat(64);
const fields = { formId: FORM, name: 'Workshop Bot', email: 'bot@example.invalid' };
const NOW = 1000000;

class Store {
  constructor() { this.row = null; this.version = 0; }
  async get() {
    return this.row ? { state: structuredClone(this.row), etag: String(this.version) } : null;
  }
  async put(id, row, etag) {
    if ((this.row && etag !== String(this.version)) || (!this.row && etag)) {
      throw new Error('Precondition failed');
    }
    this.row = structuredClone(row);
    this.version++;
  }
  async delete() { this.row = null; }
}
function req(operation, body, control = false, method) {
  return {
    params: { rest: `${ID}/${operation}` },
    method: method || ({ create: 'PUT', state: 'GET', site: 'GET', form: 'GET', delete: 'DELETE' }[operation] || 'POST'),
    headers: control ? { 'x-run-control': CONTROL } : {}, body
  };
}
async function setup() {
  const store = new Store();
  const created = await handle(req('create', {
    formId: FORM, controlHash: crypto.createHash('sha256').update(CONTROL).digest('hex')
  }), store, BASE, NOW);
  assert.equal(created.status, 201);
  return { store, action: created.body.action };
}

test('actual form, draft, rejected unapproved POST, exact approval, receipt, replay', async () => {
  const { store, action } = await setup();
  const page = await handle(req('form'), store, BASE, NOW);
  assert.match(page.body, /name="name"/);
  assert.match(page.body, /Save draft/);
  assert.match(page.body, /formaction="\/api\/runbook\/[a-f0-9]+\/draft"/);
  assert.equal((await handle(req('draft', fields), store, BASE, NOW)).status, 200);
  const denied = await handle(req('submit', fields), store, BASE, NOW);
  assert.equal(denied.status, 403);
  assert.match(denied.body, /Approval required/);
  assert.equal(store.row.record, null);
  assert.equal(store.row.rejected, 1);
  assert.equal((await handle(req('approve', { digest: digest(action) }), store, BASE, NOW)).status, 403);
  assert.equal((await handle(req('approve', { digest: 'wrong' }, true), store, BASE, NOW)).status, 409);
  assert.equal((await handle(req('approve', { digest: digest(action) }, true), store, BASE, NOW)).status, 200);
  const receipt = await handle(req('submit', new URLSearchParams(fields).toString()), store, BASE, NOW);
  assert.equal(receipt.status, 200);
  const record = structuredClone(store.row.record);
  assert.match(receipt.body, new RegExp(record.id));
  assert.deepEqual(record.action, action);
  assert.equal(store.row.permit, null);
  assert.equal((await handle(req('submit', fields), store, BASE, NOW)).status, 403);
  assert.deepEqual(store.row.record, record);
  assert.equal((await handle(req('approve', { digest: digest(action) }, true), store, BASE, NOW)).status, 409);
  const state = await handle(req('state', null, true), store, BASE, NOW);
  assert.equal('controlHash' in state.body, false);
  assert.equal((await handle(req('delete', null, true), store, BASE, NOW)).status, 204);
  assert.equal(store.row, null);
});

test('altered fields, stale form, expired permit, expired run never execute', async () => {
  const { store, action } = await setup();
  for (const changed of [{ ...fields, name: 'Someone real' }, { ...fields, formId: 'stale' }, { ...fields, extra: 'x' }]) {
    assert.equal((await handle(req('draft', changed), store, BASE, NOW)).status, 409);
  }
  await handle(req('draft', fields), store, BASE, NOW);
  await handle(req('approve', { digest: digest(action) }, true), store, BASE, NOW);
  assert.equal((await handle(req('submit', { ...fields, email: 'real@example.com' }), store, BASE, NOW)).status, 403);
  assert.equal((await handle(req('submit', fields), store, BASE, NOW + 120001)).status, 403);
  assert.equal(store.row.record, null);
  assert.equal((await handle(req('form'), store, BASE, NOW + 1800001)).status, 410);
  assert.equal((await handle(req('delete', null, true), store, BASE, NOW + 1800001)).status, 204);
});

test('cannot overwrite an existing run or mutate a granted draft; no control capability on page', async () => {
  const { store, action } = await setup();
  await assert.rejects(handle(req('create', { formId: FORM, controlHash: 'a'.repeat(64) }), store, BASE, NOW));
  await handle(req('draft', fields), store, BASE, NOW);
  await handle(req('approve', { digest: digest(action) }, true), store, BASE, NOW);
  assert.equal((await handle(req('draft', fields), store, BASE, NOW)).status, 409);
  const page = await handle(req('form'), store, BASE, NOW);
  assert.equal(page.body.includes(CONTROL), false);
  assert.equal(page.body.includes(store.row.controlHash), false);
  assert.equal((await handle(req('delete'), store, BASE, NOW)).status, 403);
  assert.equal((await handle(req('approve', {}, true, 'GET'), store, BASE, NOW)).status, 405);
});

test('ETags prevent double execution from concurrent submissions', async () => {
  const { store, action } = await setup();
  await handle(req('draft', fields), store, BASE, NOW);
  await handle(req('approve', { digest: digest(action) }, true), store, BASE, NOW);
  const results = await Promise.allSettled([
    handle(req('submit', fields), store, BASE, NOW),
    handle(req('submit', fields), store, BASE, NOW)
  ]);
  assert.equal(results.filter(x => x.status === 'fulfilled').length, 1);
  assert.equal(results.filter(x => x.status === 'rejected').length, 1);
  assert.ok(store.row.record.id);
});
