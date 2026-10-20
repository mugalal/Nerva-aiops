import {api, element, empty, panel, fields} from './components/render.js';
import {renderOverview, renderTopology} from './overview/view.js';
import {renderIncident, renderFinops} from './incident/view.js';
import {questions, renderAnswer, setOperatorAuth} from './copilot/view.js';
import {ChatStore, readUI, saveUI} from './copilot/storage.mjs';

const VIEWS={overview:'Overview',incident:'Incident Center',finops:'FinOps Summary',copilot:'Copilot'};
const $=selector=>document.querySelector(selector);
let browserStorage;try{browserStorage=window.localStorage;}catch{}
const uiState=readUI(browserStorage), busyChats=new Set();
let config, selected=uiState.selected, bundle, selectionVersion=0, chats, current='overview', knownIds=new Set();

// Routes: #overview, #incident/<id>, #finops/<id>, #copilot. Changing view pushes history so Back works.
function route(){const [name,...rest]=location.hash.slice(1).split('/');return {name:VIEWS[name]?name:null,id:rest.length?decodeURIComponent(rest.join('/')):null};}
function hashFor(name){return (name==='incident'||name==='finops')&&selected?`#${name}/${encodeURIComponent(selected)}`:`#${name}`;}
function view(name){
  if(!VIEWS[name])name='overview';current=name;
  for(const section of document.querySelectorAll('.view'))section.hidden=section.id!==name;
  for(const button of document.querySelectorAll('nav button[data-view]')){const active=button.dataset.view===name;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'page':'false');}
  $('#incident-picker').hidden=!(name==='incident'||name==='finops');
  $('#title').textContent=VIEWS[name];document.title=`NERVA | ${VIEWS[name]}`;
  uiState.view=name;saveUI(browserStorage,uiState);
  if(location.hash!==hashFor(name))history.replaceState(null,'',hashFor(name));
}
// Push the history entry, then switch synchronously so callers can focus inside the new view. Back/Forward fire hashchange.
function go(name){const hash=hashFor(name);if(location.hash!==hash)history.pushState(null,'',hash);view(name);window.scrollTo(0,0);}
function applyRoute(){const {name,id}=route();view(name||'overview');if(id&&id!==selected&&knownIds.has(id))select(id);}
for(const button of document.querySelectorAll('nav button[data-view]'))button.addEventListener('click',()=>go(button.dataset.view));
window.addEventListener('hashchange',applyRoute);

const notice=$('#notice');
function message(text,tone='warn'){notice.hidden=!text;notice.className=tone;$('#notice-text').textContent=text||'';}
$('#notice-close').addEventListener('click',()=>message(''));

function updateChatContext(){
  const chat=chats?.active,incident=chat?.turns.length?chat.incident_id:selected;
  $('#chat-context').textContent=incident?`Answers focus on ${incident}.`:'No incident selected.';
  const other=Boolean(chat?.turns.length&&selected&&chat.incident_id!==selected),switcher=$('#chat-switch');
  switcher.hidden=!other;switcher.textContent=`Start a new chat about ${selected}`;
}
function renderChatList(){
  const list=$('#chat-list');list.replaceChildren();if(!chats)return;
  for(const chat of [...chats.sessions].sort((a,b)=>b.updated_at-a.updated_at)){
    const item=element('div',undefined,'chat-item'),open=element('button',chat.title),remove=element('button','×','chat-delete');
    open.title=chat.title;open.classList.toggle('active',chat.id===chats.activeId);open.setAttribute('aria-current',chat.id===chats.activeId?'true':'false');
    open.addEventListener('click',()=>{chats.open(chat.id);renderChat(true);go('copilot');});
    remove.title=`Delete chat: ${chat.title}`;remove.setAttribute("aria-label",remove.title);remove.disabled=busyChats.has(chat.id);
    remove.addEventListener('click',()=>{if(chat.turns.length&&!confirm(`Delete "${chat.title}"? This removes it from this browser.`))return;chats.remove(chat.id);renderChat();});
    item.append(open,remove);list.append(item);
  }
}
function renderChat(scroll=false){
  const root=$('#answer');root.replaceChildren();const chat=chats?.active;if(!chat)return;
  for(const turn of chat.turns){const user=panel('You');user.classList.add('chat-user');user.append(element('p',turn.question,'answer-text'));root.append(user);if(turn.result)root.append(renderAnswer(turn.result,()=>chats.save()));else root.append(empty(turn.error|| (busyChats.has(chat.id)?'NERVA is looking up records and preparing an answer...':'The response was interrupted. Send your question again to continue.')));}
  if(!chat.turns.length)root.append(empty('Start a conversation. Ask for an explanation, compare outcomes, or discuss next steps.'));
  const busy=busyChats.has(chat.id),send=$('#send-chat');
  $('#question').value=chat.draft||'';send.disabled=busy;send.textContent=busy?'Sending...':'Send';
  updateChatContext();renderChatList();
  // Bring the newest answer's start into view rather than its end, so long answers read top-down.
  if(scroll&&current==='copilot')root.lastElementChild?.previousElementSibling?.scrollIntoView({block:'start',behavior:'smooth'});
}
function startChatFor(id){
  if(!chats)return;const chat=chats.active;
  if(chat.turns.length&&chat.incident_id!==id)chats.create(id||null);else chat.incident_id=id||null;
  chats.save();renderChat();go('copilot');$('#question').focus();
}

function placeholder(text){for(const target of ['incident-body','finops-body'])document.getElementById(target).replaceChildren(empty(text));}
async function select(id){
  selected=id;uiState.selected=id;saveUI(browserStorage,uiState);bundle=null;const version=++selectionVersion;
  $('#incident-select').value=id;
  if(current==='incident'||current==='finops')history.replaceState(null,'',hashFor(current));
  updateChatContext();placeholder('Loading incident...');
  try{const data=await api(`/internal/ui/incidents/${encodeURIComponent(id)}`);if(version!==selectionVersion)return;bundle=data;}
  catch(error){if(version!==selectionVersion)return;bundle=null;message(error.message);placeholder('Incident data unavailable.');return;}
  const similar=renderIncident(bundle,config,approve,store,()=>startChatFor(id));renderFinops(bundle);
  if(!similar)return;
  // A memory search failure only affects this panel; the incident itself stays visible.
  try{const result=await api('/internal/memory/search',{incident_type:bundle.memory.incident_type,service:bundle.memory.service,tags:bundle.memory.tags,exclude_incident_id:id,source:config.source});if(version!==selectionVersion)return;
    similar.replaceChildren(similar.firstChild);
    for(const item of result.results){const memory=item.record.memory;similar.append(fields(memory));if(knownIds.has(memory.incident_id)&&memory.incident_id!==id){const open=element('button',`Open ${memory.incident_id}`);open.addEventListener('click',()=>{select(memory.incident_id);window.scrollTo(0,0);});similar.append(open);}}
    if(!result.results.length)similar.append(empty('No similar incident stored yet.'));
  }catch(error){if(version!==selectionVersion)return;similar.replaceChildren(similar.firstChild,empty(`Similar incidents unavailable: ${error.message}`));}
}
async function load(){
  const refresh=$('#refresh');refresh.disabled=true;refresh.textContent='Refreshing...';message('');
  try{config=await api('/internal/ui/config');setOperatorAuth(config.operator_auth);const badge=$('#mode');badge.textContent=config.source==='mock'?'MOCK DATA · demo only':'LIVE API';badge.className=`badge ${config.source}`;
    if(!chats||chats.source!==config.source){chats=new ChatStore(browserStorage,config.source,message);renderChat();}
    $('#chat-mode').textContent=config.chat?.enabled?'AI chat':'AI connection needed · recorded answers available';
    const data=await api('/internal/ui/overview');knownIds=new Set(data.incidents.map(r=>r.incident_id));
    renderOverview(data,id=>{select(id);go('incident');});
    try{renderTopology((await api('/internal/ui/services')).services,id=>{select(id);go('incident');});}catch{renderTopology(null);}
    const awaiting=data.incidents.filter(r=>r.status==='AWAITING_APPROVAL').length,badgeCount=$('#nav-attention');badgeCount.hidden=!awaiting;badgeCount.textContent=String(awaiting);badgeCount.title=`${awaiting} awaiting your decision`;
    const picker=$('#incident-select');picker.replaceChildren();for(const record of data.incidents){const option=element('option',`${record.incident_id} · ${(record.status||'UNKNOWN').replaceAll('_',' ')}`);option.value=record.incident_id;picker.append(option);}
    $('#updated').textContent=`Updated ${new Date().toLocaleTimeString()}`;
    if(data.status==='degraded')message(data.reason||'Shared API returned degraded data.');
    if(data.incidents.length)await select(knownIds.has(selected)?selected:data.incidents[0].incident_id);
    else{selected=null;bundle=null;placeholder('No incident selected.');}
  }catch(error){message(error.message);selected=null;bundle=null;knownIds=new Set();$('#summary').replaceChildren();$('#incidents').replaceChildren(empty('Shared API unavailable. No mock fallback was substituted.'));$('#incident-select').replaceChildren();placeholder('Data unavailable.');}
  finally{refresh.disabled=false;refresh.textContent='Refresh';}
}
async function approve(decision,button,review={}){const incidentId=selected;button.disabled=true;try{const headers=review.token?{'X-Nexus-Operator-Token':review.token}:{};const result=await api(`/internal/ui/incidents/${encodeURIComponent(incidentId)}/approval`,{decision,...(review.digest?{proposal_digest:review.digest}:{})},310000,headers);message(`M4 ${decision} response: ${result.status||'received'}. Recovery must be checked separately.`,'ok');if(selected===incidentId)await select(incidentId);}catch(error){message(error.message+' Refresh the incident before retrying; the server may already have received the decision.');if(selected===incidentId)await select(incidentId);}finally{button.disabled=false;}}
async function store(button){button.disabled=true;try{const context={};for(const key of ['incident','anomaly','rca','decision','action_result','recovery_result','finops_context','deployment_event','mttd_seconds','mttr_seconds','cost_impact','slo_impact'])if(bundle[key]!==undefined)context[key]=bundle[key];const result=await api('/internal/memory/store',{memory:bundle.memory,context,source:bundle.source==='mock'?'mock':'real',resolved:bundle.incident?.status==='RESOLVED'});message(`Memory ${result.status}: ${selected}`,'ok');}catch(error){message(error.message);}finally{button.disabled=false;}}
async function ask(question){question=question.trim();const store=chats,chat=store?.active;if(!question||!chat||busyChats.has(chat.id))return;const previous=store.history(chat);if(!chat.turns.length){chat.title=question.slice(0,65);chat.incident_id=selected||null;}const turn={question,result:null,error:null};chat.turns.push(turn);chat.draft='';chat.updated_at=Date.now();busyChats.add(chat.id);store.save();renderChat(true);try{turn.result=await api('/internal/copilot/chat',{question,incident_id:chat.incident_id,source:chat.source,history:previous},150000);}catch(error){turn.error=error.message;}finally{busyChats.delete(chat.id);chat.updated_at=Date.now();store.save();if(chats===store){if(chats.activeId===chat.id)renderChat(true);else renderChatList();}}}
$('#incident-select').addEventListener('change',event=>select(event.target.value));
$('#refresh').addEventListener('click',load);
$('#query-form').addEventListener('submit',event=>{event.preventDefault();ask($('#question').value);});
$('#new-chat').addEventListener('click',()=>{if(!chats)return;chats.create(selected||null);renderChat();$('#question').focus();});
$('#chat-switch').addEventListener('click',()=>startChatFor(selected));
$('#question').addEventListener('input',event=>{if(chats?.active){chats.active.draft=event.target.value;chats.save();}});
for(const question of questions){const button=element('button',question);button.addEventListener('click',()=>{$('#question').value=question;ask(question);});$('#question-buttons').append(button);}
const initial=route();if(initial.id)selected=initial.id;
view(initial.name||uiState.view||'overview');
load();
