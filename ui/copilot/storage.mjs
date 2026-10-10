const key = source => `nexus.chats.v1.${source}`;

export class ChatStore {
  constructor(storage, source, onError = () => {}) {
    this.storage = storage;
    this.source = source;
    this.onError = onError;
    this.sessions = [];
    try {
      const saved = JSON.parse(storage?.getItem(key(source)) || 'null');
      this.sessions = (saved?.sessions || []).filter(chat => chat && typeof chat.id === 'string' &&
        chat.source === source && typeof chat.title === 'string' && Array.isArray(chat.turns) &&
        chat.turns.every(turn => typeof turn.question === 'string' &&
          (!turn.result || (typeof turn.result.answer === 'string' && turn.result.source === source &&
            Array.isArray(turn.result.citations)))));
      this.activeId = saved?.activeId;
    } catch { this.onError('Saved chats could not be loaded. Your new conversation will still work.'); }
    if (!this.active) this.create();
  }
  get active() { return this.sessions.find(chat => chat.id === this.activeId); }
  save() {
    try {
      if (!this.storage) throw new Error('Browser storage unavailable');
      this.storage.setItem(key(this.source), JSON.stringify({activeId: this.activeId, sessions: this.sessions}));
    } catch { this.onError('Chats are available for this visit, but browser storage is unavailable or full. They may not survive refresh.'); }
  }
  create(incidentId = null) {
    const chat = {id: crypto.randomUUID(), source: this.source, title: 'New chat',
      incident_id: incidentId, turns: [], draft: '', updated_at: Date.now()};
    this.sessions.unshift(chat);
    this.activeId = chat.id;
    this.save();
    return chat;
  }
  open(id) {
    if (!this.sessions.some(chat => chat.id === id)) return;
    this.activeId = id;
    this.save();
  }
  history(chat = this.active) {
    return chat.turns.filter(turn => turn.result && turn.result.status !== 'ai_unavailable')
      .flatMap(turn => [{role: 'user', content: turn.question},
        {role: 'assistant', content: turn.result.answer}]).slice(-20);
  }
}

export function readUI(storage) {
  try { return JSON.parse(storage?.getItem('nexus.ui.v1') || '{}') || {}; }
  catch { return {}; }
}
export function saveUI(storage, state) {
  try { storage?.setItem('nexus.ui.v1', JSON.stringify(state)); } catch { /* ChatStore reports storage failures. */ }
}
