import {element, empty} from '../components/render.js';
export function renderOverview(data, onSelect) {
  const records = data.incidents;
  const summary = document.querySelector('#summary'); summary.replaceChildren();
  for (const [label,count] of [['Recorded incidents',records.length],['Open incidents',records.filter(r=>r.status!=='RESOLVED').length],['Resolved incidents',records.filter(r=>r.status==='RESOLVED').length]]) {
    const card=element('div',undefined,'card'); card.append(element('span',label,'muted'),element('strong',String(count))); summary.append(card);
  }
  const container=document.querySelector('#incidents'); container.replaceChildren();
  if (!records.length) {container.append(empty('No incidents returned. This does not establish service health.'));return;}
  const table=element('table'),head=element('tr');
  for(const label of ['Incident','Services','Status',''])head.append(element('th',label));table.append(head);
  for(const record of records){const row=element('tr'),cell=element('td'),button=element('button','Open');button.addEventListener('click',()=>onSelect(record.incident_id));cell.append(button);row.append(element('td',record.incident_id),element('td',(record.affected_services||[]).join(', ')),element('td',record.status||'Unknown'),cell);table.append(row);}container.append(table);
}
