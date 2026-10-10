import test from 'node:test';
import assert from 'node:assert/strict';
import {ChatStore, readUI, saveUI} from './storage.mjs';

function storage() {
  const data = new Map();
  return {getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value)};
}
const result = answer => ({source: 'mock', status: 'ok', answer, citations: [], action_requests: []});

test('refresh restores full conversation, draft, selected chat and action decision', () => {
  const browser = storage(), store = new ChatStore(browser, 'mock');
  store.active.title = 'Restart request';
  store.active.incident_id = 'INC-001';
  store.active.draft = 'My follow-up';
  store.active.turns.push({question: 'Restart it?', result: {...result('Review this request'),
    action_requests: [{draft_id: 'id', decision: 'declined', decision_status: 'Declined'}]}});
  store.save();
  const restored = new ChatStore(browser, 'mock');
  assert.deepEqual(restored.active, store.active);
  assert.equal(restored.history()[1].content, 'Review this request');
});

test('New chat keeps earlier conversations and switching restores their independent history', () => {
  const browser = storage(), store = new ChatStore(browser, 'mock');
  const first = store.active;
  first.turns.push({question: 'First question', result: result('First answer')});
  store.create('INC-DEMO-TRAFFIC');
  assert.equal(store.history().length, 0);
  assert.equal(store.sessions.length, 2);
  store.open(first.id);
  assert.equal(store.history()[0].content, 'First question');
  assert.equal(new ChatStore(browser, 'mock').activeId, first.id);
});

test('mock and live chats remain isolated and incomplete turns are not replayed', () => {
  const browser = storage(), mock = new ChatStore(browser, 'mock');
  mock.active.turns.push({question: 'Interrupted', result: null});
  mock.save();
  const live = new ChatStore(browser, 'real');
  assert.notEqual(live.activeId, mock.activeId);
  assert.equal(live.history().length, 0);
  assert.equal(new ChatStore(browser, 'mock').history().length, 0);
});

test('page and selected incident survive refresh; malformed/unavailable storage stays usable', () => {
  const browser = storage();
  saveUI(browser, {view: 'copilot', selected: 'INC-001'});
  assert.deepEqual(readUI(browser), {view: 'copilot', selected: 'INC-001'});
  browser.setItem('nexus.chats.v1.mock', 'invalid JSON');
  const errors = [];
  assert.equal(new ChatStore(browser, 'mock', message => errors.push(message)).sessions.length, 1);
  const unavailable = new ChatStore(null, 'mock', message => errors.push(message));
  unavailable.create();
  assert.equal(unavailable.sessions.length, 2);
  assert.ok(errors.length > 0);
});
