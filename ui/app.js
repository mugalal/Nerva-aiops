import {api, element, empty, panel, fields} from './components/render.js?v=20261010-chats';
import {renderOverview} from './overview/view.js';
import {renderIncident, renderFinops} from './incident/view.js';
import {questions, renderAnswer} from './copilot/view.js?v=20261010-chats';
import {ChatStore, readUI, saveUI} from './copilot/storage.mjs';

let browserStorage;try{browserStorage=window.localStorage;}catch{}
const uiState=readUI(browserStorage), busyChats=new Set();
let config, selected=uiState.selected, bundle, selectionVersion=0, chats;
function updateChatContext(){document.querySelector('#chat-context').textContent=`Incident: ${chats?.active?.incident_id||selected||'none selected'}`;}
function renderChatList(){const list=document.querySelector('#chat-list');list.replaceChildren();if(!chats)return;for(const chat of [...chats.sessions].sort((a,b)=>b.updated_at-a.updated_at)){const button=element('button',chat.title);button.title=chat.title;button.classList.toggle('active',chat.id===chats.activeId);button.setAttribute('aria-current',chat.id===chats.activeId?'true':'false');button.addEventListener('click',()=>{chats.open(chat.id);renderChat();view('copilot');});list.append(button);}}
function renderChat(){const root=document.querySelector('#answer');root.replaceChildren();const chat=chats?.active;if(!chat)return;for(const turn of chat.turns){const user=panel('You');user.classList.add('chat-user');user.append(element('p',turn.question,'answer-text'));root.append(user);if(turn.result)root.append(renderAnswer(turn.result,()=>chats.save()));else root.append(empty(turn.error|| (busyChats.has(chat.id)?'NEXUS is looking up records and preparing an answer...':'The response was interrupted. Send your question again to continue.')));}if(!chat.turns.length)root.append(empty('Start a conversation. Ask for an explanation, compare outcomes, or discuss next steps.'));document.querySelector('#question').value=chat.draft||'';document.querySelector('#send-chat').disabled=busyChats.has(chat.id);updateChatContext();renderChatList();}
const notice=document.querySelector('#notice');
function message(text){notice.hidden=!text;notice.textContent=text||'';}
function view(name){if(!['overview','incident','finops','copilot'].includes(name))name='overview';for(const section of document.querySelectorAll('.view'))section.hidden=section.id!==name;for(const button of document.querySelectorAll('nav button[data-view]'))button.classList.toggle('active',button.dataset.view===name);document.querySelector('#title').textContent={overview:'Overview',incident:'Incident Center',finops:'FinOps Summary',copilot:'Copilot'}[name];uiState.view=name;saveUI(browserStorage,uiState);if(location.hash!==`#${name}`)history.replaceState(null,'',`#${name}`);}
for(const button of document.querySelectorAll('nav button[data-view]'))button.addEventListener('click',()=>view(button.dataset.view));
window.addEventListener('hashchange',()=>view(location.hash.slice(1)));
async function select(id){
  selected=id;uiState.selected=id;saveUI(browserStorage,uiState);bundle=null;const version=++selectionVersion;
  document.querySelector('#incident-select').value=id;
  updateChatContext();
  for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Loading incident...'));
  try{const data=await api(`/internal/ui/incidents/${encodeURIComponent(id)}`);if(version!==selectionVersion)return;bundle=data;
    renderIncident(bundle,config,approve,store);renderFinops(bundle);
    if(bundle.memory){const similar=await api('/internal/memory/search',{incident_type:bundle.memory.incident_type,service:bundle.memory.service,tags:bundle.memory.tags,exclude_incident_id:id,source:config.source});if(version!==selectionVersion)return;const box=panel('Similar incidents');for(const item of similar.results)box.append(fields(item.record.memory));if(!similar.results.length)box.append(empty('No similar incident stored yet.'));document.querySelector('#incident-body').append(box);}
  }catch(error){if(version!==selectionVersion)return;bundle=null;message(error.message);for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Incident data unavailable.'));}
}
async function load(){
  message('');
  try{config=await api('/internal/ui/config');const badge=document.querySelector('#mode');badge.textContent=config.source==='mock'?'MOCK DATA · demo only':'LIVE API';badge.className=`badge ${config.source}`;
    if(!chats||chats.source!==config.source){chats=new ChatStore(browserStorage,config.source,message);renderChat();}
    document.querySelector('#chat-mode').textContent=config.chat?.enabled?'AI chat':'AI connection needed · recorded answers available';
    const data=await api('/internal/ui/overview');renderOverview(data,id=>{select(id);view('incident');});
    const picker=document.querySelector('#incident-select');picker.replaceChildren();for(const record of data.incidents){const option=element('option',record.incident_id);option.value=record.incident_id;picker.append(option);}
    if(data.incidents.length)await select(data.incidents.some(r=>r.incident_id===selected)?selected:data.incidents[0].incident_id);
    else{selected=null;bundle=null;for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('No incident selected.'));}
    if(data.status==='degraded')message(data.reason||'Shared API returned degraded data.');
  }catch(error){message(error.message);selected=null;bundle=null;document.querySelector('#summary').replaceChildren();document.querySelector('#incidents').replaceChildren(empty('Shared API unavailable. No mock fallback was substituted.'));document.querySelector('#incident-select').replaceChildren();for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty('Data unavailable.'));}
}
async function approve(decision,button){button.disabled=true;try{const result=await api(`/internal/ui/incidents/${encodeURIComponent(selected)}/approval`,{decision});message(`M4 approval response: ${result.status||'received'}. Execution and recovery must be checked separately.`);await select(selected);}catch(error){message(error.message);}finally{button.disabled=false;}}
async function store(button){button.disabled=true;try{const context={};for(const key of ['incident','anomaly','rca','decision','action_result','recovery_result','finops_context','deployment_event','mttd_seconds','mttr_seconds','cost_impact','slo_impact'])if(bundle[key]!==undefined)context[key]=bundle[key];const result=await api('/internal/memory/store',{memory:bundle.memory,context,source:config.source,resolved:true});message(`Memory ${result.status}: ${selected}`);}catch(error){message(error.message);}finally{button.disabled=false;}}
async function ask(question){question=question.trim();const store=chats,chat=store?.active;if(!question||!chat||busyChats.has(chat.id))return;const previous=store.history(chat);if(!chat.turns.length){chat.title=question.slice(0,65);chat.incident_id=selected||null;}const turn={question,result:null,error:null};chat.turns.push(turn);chat.draft='';chat.updated_at=Date.now();busyChats.add(chat.id);store.save();renderChat();try{turn.result=await api('/internal/copilot/chat',{question,incident_id:chat.incident_id,source:chat.source,history:previous},150000);}catch(error){turn.error=error.message;}finally{busyChats.delete(chat.id);chat.updated_at=Date.now();store.save();if(chats===store){if(chats.activeId===chat.id)renderChat();else renderChatList();}}}
document.querySelector('#incident-select').addEventListener('change',event=>select(event.target.value));
document.querySelector('#refresh').addEventListener('click',load);
document.querySelector('#query-form').addEventListener('submit',event=>{event.preventDefault();ask(document.querySelector('#question').value);});
document.querySelector('#new-chat').addEventListener('click',()=>{if(!chats)return;chats.create(selected||null);renderChat();});
document.querySelector('#question').addEventListener('input',event=>{if(chats?.active){chats.active.draft=event.target.value;chats.save();}});
for(const question of questions){const button=element('button',question);button.addEventListener('click',()=>{document.querySelector('#question').value=question;ask(question);});document.querySelector('#question-buttons').append(button);}
view(location.hash.slice(1)||uiState.view||'overview');
load();
