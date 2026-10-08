export function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
export function empty(text = 'No recorded data is available.') { return element('div', text, 'empty'); }
export function panel(title) {
  const node = element('article', undefined, 'panel');
  node.append(element('h3', title)); return node;
}
export function value(data) {
  if (data === null || data === undefined) return 'Not recorded';
  if (Array.isArray(data)) return data.map(value).join(', ');
  if (typeof data === 'object') return Object.entries(data).map(([k,v]) => `${k.replaceAll('_',' ')}: ${value(v)}`).join('; ');
  return String(data);
}
export function fields(data, labels = {}) {
  if (!data || !Object.keys(data).length) return empty();
  const table = element('table');
  for (const [key, item] of Object.entries(data)) {
    const row = element('tr'); row.append(element('th', labels[key] || key.replaceAll('_', ' ')), element('td', value(item))); table.append(row);
  }
  return table;
}
export async function api(path, payload) {
  const response = await fetch(path, {method: payload ? 'POST' : 'GET', headers: payload ? {'Content-Type':'application/json'} : {}, body: payload ? JSON.stringify(payload) : undefined, signal: AbortSignal.timeout(10000)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  return data;
}
