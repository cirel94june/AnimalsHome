const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function main() {
  const requests = [];
  let completeStop;
  const context = {
    window: {}, AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url.includes('/abort?')) return new Promise(resolve => { completeStop = () => resolve({ ok: true, json: async () => ({ ok: true, stopped: true, messages: [], message_ids: [] }) }); });
      return { ok: true, json: async () => ({ active: false }) };
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(`${__dirname}/static/generation-control.js`, 'utf8'), context);
  const Controller = context.window.ChatGenerationControl;
  const preparation = [];
  const progress = new Controller({ surface: 'private', baseUrl: id => `/rooms/${id}`,
    onPreparing: (sender, generation) => preparation.push([sender, generation.id]) });
  const preparing = progress.begin('progress');
  await progress.fetch(preparing, '/send', { method: 'POST' });
  assert.equal(preparation.at(-1)?.[0], '', 'preparation must appear before model start');
  progress.accepts({ type: 'preparing', sender: 'connor', generation_id: preparing.id }, preparing);
  assert.equal(preparation.at(-1)[0], 'connor');
  progress.accepts({ type: 'start', id: 'reply', generation_id: preparing.id }, preparing);
  assert.equal(preparation.at(-1)[0], null, 'real start removes preparation');
  const savedFetch = context.fetch;
  context.fetch = async () => { throw new Error('offline'); };
  await assert.rejects(progress.fetch(preparing, '/send', {}), /offline/);
  assert.equal(preparation.at(-1)[0], null, 'HTTP failure removes preparation');
  context.fetch = savedFetch;
  requests.length = 0;
  let stopped = 0, finished = 0;
  const control = new Controller({ surface: 'chatroom', baseUrl: id => `/rooms/${id}`, onStop: () => stopped++, onFinish: () => finished++ });
  const old = control.begin('old-room');
  await control.fetch(old, '/send', { method: 'POST' });
  assert.equal(requests[0].options.headers['X-Generation-Id'], old.id);
  assert.equal(requests[0].options.headers['X-Generation-Target'], 'old-room');
  const stopping = control.stop();
  assert.equal(stopped, 1);
  assert.equal(old.controller.signal.aborted, true);
  assert.equal(control.accepts({ generation_id: old.id, type: 'tts_chunk' }), false);
  assert.equal(control.accepts({ type: 'chunk' }, old), false);
  const next = control.begin('new-room');
  completeStop();
  await stopping;
  assert.equal(control.active, next);
  assert.equal(next.controller.signal.aborted, false);
  assert.equal(finished, 0);
  assert.match(requests[1].url, /^\/rooms\/old-room\/abort\?generation_id=/);
  await control.finish(old);
  assert.equal(control.active, next);
  await control.finish(next);
  assert.equal(control.active, null);
  assert.equal(finished, 1);

  const retry = control.begin('retry-room');
  context.fetch = async () => { throw new Error('offline'); };
  await control.stop();
  assert.equal(control.retryStop, retry);
  assert.equal(finished, 1, 'network failure must not claim backend completion');
  console.log('generation control: cancellation, late events, new-send isolation and retry passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
