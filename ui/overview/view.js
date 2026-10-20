import {element, empty, statusPill, formatTime} from '../components/render.js';
// Incidents needing a decision come first, then other open incidents, then resolved; newest first within each group.
const RANK = status => status === 'AWAITING_APPROVAL' ? 0 : status !== 'RESOLVED' ? 1 : 2;
export function sortIncidents(records) {
  return [...records].sort((a, b) => RANK(a.status) - RANK(b.status) || (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
}
// Callers first, then what they depend on: checkout-service -> payment-service.
function orderByDependency(services) {
  const byName = new Map(services.map(s => [s.name, s]));
  const depth = name => { const s = byName.get(name); return s && s.depends_on?.length ? 1 + Math.max(...s.depends_on.map(depth)) : 0; };
  return [...services].sort((a, b) => depth(b.name) - depth(a.name) || a.name.localeCompare(b.name));
}

// State comes only from recorded incidents; "no open incident" is not a health claim.
export function renderTopology(services, onSelect) {
  const root = document.querySelector('#topology'); if (!root) return; root.replaceChildren();
  if (!Array.isArray(services)) { root.append(empty('Service topology unavailable.')); return; }
  const strip = element('div', undefined, 'topology');
  orderByDependency(services).forEach((service, index) => {
    if (index) strip.append(element('span', '→', 'topology-edge'));
    const open = (service.open_incidents || []).filter(i => i.kind !== 'rightsizing');
    const node = element('button', undefined, `topology-node ${open.length ? 'incident' : 'quiet'}`);
    node.append(element('strong', service.name),
      element('span', open.length ? `${open.length} open incident${open.length > 1 ? 's' : ''}: ${open[0].status.replaceAll('_', ' ')}` : 'No open incident', 'muted'));
    if (service.depends_on?.length) node.append(element('span', `calls ${service.depends_on.join(', ')}`, 'muted'));
    if (service.latest_dependency_block) node.append(element('span', `Last escalation: blocked by ${service.latest_dependency_block.dependency}`, 'muted'));
    node.disabled = !open.length;
    if (open.length) node.addEventListener('click', () => onSelect(open[0].incident_id));
    strip.append(node);
  });
  root.append(element('h2', 'Services'), strip);
}

export function renderOverview(data, onSelect) {
  const records = sortIncidents(data.incidents);
  const summary = document.querySelector('#summary'); summary.replaceChildren();
  const awaiting = records.filter(r => r.status === 'AWAITING_APPROVAL');
  for (const [label,count,tone] of [['Recorded incidents',records.length],['Open incidents',records.filter(r=>r.status!=='RESOLVED').length],['Awaiting your decision',awaiting.length,awaiting.length?'action':''],['Resolved incidents',records.filter(r=>r.status==='RESOLVED').length]]) {
    const card=element('div',undefined,`card ${tone||''}`); card.append(element('span',label,'muted'),element('strong',String(count)));
    if(tone){const go=element('button','Review next','primary');go.addEventListener('click',()=>onSelect(awaiting[0].incident_id));card.append(go);}
    summary.append(card);
  }
  const container=document.querySelector('#incidents'); container.replaceChildren();
  if (!records.length) {container.append(empty('No incidents returned. This does not establish service health.'));return;}
  const table=element('table',undefined,'incident-table'),head=element('tr');
  for(const label of ['Incident','Severity','Services','Started','Status',''])head.append(element('th',label));table.append(head);
  for(const record of records){
    const row=element('tr',undefined,'clickable'),cell=element('td'),button=element('button','Open');
    button.addEventListener('click',event=>{event.stopPropagation();onSelect(record.incident_id);});row.addEventListener('click',()=>onSelect(record.incident_id));
    const status=element('td');status.append(statusPill(record.status));cell.append(button);
    row.append(element('td',record.incident_id,'strong'),element('td',record.severity||'—'),element('td',(record.affected_services||[]).join(', ')),element('td',record.started_at?formatTime(record.started_at):'—','nowrap'),status,cell);table.append(row);
  }
  container.append(table);
}
