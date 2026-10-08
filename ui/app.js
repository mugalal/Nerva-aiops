import {api, element, empty, panel, fields} from './components/render.js';
import {renderOverview} from './overview/view.js';
import {renderIncident, renderFinops} from './incident/view.js';
import {questions, renderAnswer} from './copilot/view.js';

let config, selected, bundle, selectionVersion=0;
const notice=document.querySelector('#notice');
function message(text){notice.hidden=!text;notice.textContent=text||'';}
function view(name){for(const section of document.querySelectorAll('.view'))section.hidden=section.id!==name;for(const button of document.querySelectorAll('nav button'))button.classList.toggle('active',button.dataset.view===name);document.querySelector('#title').textContent={overview:'Overview',incident:'Incident Center',finops:'FinOps Summary',copilot:'Copilot'}[name];}
for(const button of document.querySelectorAll('nav button'))button.addEventListener('click',()=>view(button.dataset.view));
async function select(id){
  selected=id;bundle=null;const version=++selectionVersion;
  document.querySelector('#incident-select').value=id;
  document.querySelector('#answer').replaceChildren();
  for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Loading incident...'));
  try{const data=await api(`/internal/ui/incidents/${encodeURIComponent(id)}`);if(version!==selectionVersion)return;bundle=data;
    renderIncident(bundle,config,approve,store);renderFinops(bundle);
    if(bundle.memory){const similar=await api('/internal/memory/search',{incident_type:bundle.memory.incident_type,service:bundle.memory.service,tags:bundle.memory.tags,exclude_incident_id:id,source:config.source});if(version!==selectionVersion)return;const box=panel('Similar incidents');for(const item of similar.results)box.append(fields(item.record.memory));if(!similar.results.length)box.append(empty('No similar incident stored yet.'));document.querySelector('#incident-body').append(box);}
  }catch(error){if(version!==selectionVersion)return;bundle=null;message(error.message);for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Incident data unavailable.'));}
}
async function load(){
  message('');
  try{config=await api('/internal/ui/config');const badge=document.querySelector('#mode');badge.textContent=config.source==='mock'?'MOCK DATA · demo only':'LIVE API';badge.className=`badge ${config.source}`;
    const data=await api('/internal/ui/overview');renderOverview(data,id=>{select(id);view('incident');});
    const picker=document.querySelector('#incident-select');picker.replaceChildren();for(const record of data.incidents){const option=element('option',record.incident_id);option.value=record.incident_id;picker.append(option);}
    if(data.incidents.length)await select(data.incidents.some(r=>r.incident_id===selected)?selected:data.incidents[0].incident_id);
    else{selected=null;bundle=null;for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('No incident selected.'));}
    if(data.status==='degraded')message(data.reason||'Shared API returned degraded data.');
  }catch(error){message(error.message);selected=null;bundle=null;document.querySelector('#summary').replaceChildren();document.querySelector('#incidents').replaceChildren(empty('Shared API unavailable. No mock fallback was substituted.'));document.querySelector('#incident-select').replaceChildren();for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Data unavailable.'));}
}
async function approve(decision,button){button.disabled=true;try{const result=await api(`/internal/ui/incidents/${encodeURIComponent(selected)}/approval`,{decision});message(`M4 approval response: ${result.status||'received'}. Execution and recovery must be checked separately.`);await select(selected);}catch(error){message(error.message);}finally{button.disabled=false;}}
async function store(button){button.disabled=true;try{const context={};for(const key of ['incident','anomaly','rca','decision','action_result','recovery_result','finops_context','deployment_event','mttd_seconds','mttr_seconds','cost_impact','slo_impact'])if(bundle[key]!==undefined)context[key]=bundle[key];const result=await api('/internal/memory/store',{memory:bundle.memory,context,source:config.source,resolved:true});message(`Memory ${result.status}: ${selected}`);}catch(error){message(error.message);}finally{button.disabled=false;}}
async function ask(question){const root=document.querySelector('#answer');root.replaceChildren(empty('Retrieving recorded evidence...'));if(!selected){root.replaceChildren(empty('Select an incident first.'));return;}const id=selected,version=selectionVersion;try{const result=await api('/internal/copilot/query',{question,incident_id:id,source:config.source});if(version===selectionVersion)renderAnswer(result);}catch(error){if(version===selectionVersion)root.replaceChildren(empty(error.message));}}
document.querySelector('#incident-select').addEventListener('change',event=>select(event.target.value));
document.querySelector('#refresh').addEventListener('click',load);
document.querySelector('#query-form').addEventListener('submit',event=>{event.preventDefault();ask(document.querySelector('#question').value);});
for(const question of questions){const button=element('button',question);button.addEventListener('click',()=>{document.querySelector('#question').value=question;ask(question);});document.querySelector('#question-buttons').append(button);}
load();
