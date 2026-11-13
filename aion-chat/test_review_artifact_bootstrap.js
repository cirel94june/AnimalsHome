'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

for (const filename of ['chat.js', 'chatroom.js']) {
  test(`${filename}: helper arriving during a reply updates old code when idle`, () => {
    const source = fs.readFileSync(path.join(__dirname, 'static', filename), 'utf8');
    const start = source.indexOf('if (!window.ChatWorks) {');
    assert.ok(start >= 0);
    const end = source.indexOf('\n}', start) + 2;
    const timers = [], appended = [];
    let renders = 0;
    const context = vm.createContext({
      window: {}, document: {createElement: tag => ({tag}), head: {append: (...items) => appended.push(...items)}},
      sending: true, streamingAiId: 'in-flight', currentConvId: 'c',
      isSending: true, isAiChatting: true, currentRoom: {id:'r'}, crMessagesById: {old: {id:'old', created_at:1}},
      renderMessages: () => renders++,
      setTimeout: fn => timers.push(fn), clearTimeout: () => {},
    });
    vm.runInContext(source.slice(start, end), context);
    context.window.ChatWorks = {prepare: () => ({card:'test'})};
    appended.find(item => item.tag === 'script').onload();
    assert.equal(renders, 0, 'must preserve an in-flight reply');
    context.sending = context.isSending = context.isAiChatting = false;
    context.streamingAiId = null;
    for (let round = 0; timers.length && round < 10; round++) timers.shift()();
    assert.equal(renders, 1, 'existing HTML must become a card after the reply finishes, without reopening');
  });
}
