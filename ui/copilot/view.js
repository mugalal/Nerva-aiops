import {api, element, panel, fields} from '../components/render.js?v=20261010-chats';
export const questions=['Show me all incidents','Show unresolved incidents','Which services are unhealthy?','What is the root cause?','Have we seen this before?','Did it work?'];
export function renderAnswer(result,onActionChange=()=>{}){const box=panel('NEXUS');box.classList.add('chat-assistant');box.append(element('span',result.mode==='ai_chat'?(result.kind==='general_guidance'?'General guidance':`Recorded evidence · ${result.source}`):result.mode==='tool_results'?'Tool results':'Recorded-answer fallback','badge'),element('p',result.answer,'answer-text'));if(result.warning)box.append(element('p',result.warning,'muted'));
for(const item of result.tool_results||[]){if(item.tool==='list_incidents'&&item.result.incidents){const list=panel('Incidents');list.append(element('p',`${item.result.total_matches} matching incidents · ${item.result.source}`,'muted'));for(const row of item.result.incidents)list.append(fields(row));for(const note of item.result.notices||[])list.append(element('p',note,'muted'));if(item.result.next_offset!==null)list.append(element('p',`More results available. Ask for the next page at offset ${item.result.next_offset}.`,'muted'));box.append(list);}}
const toolTitles={get_system_overview:'System access',list_services:'Services',get_telemetry:'Metric snapshots',get_deployments:'Deployment history',get_finops:'Cost and resource evidence',get_logs:'Log sample',get_action_status:'Action request status',get_service_health:'Service health'};
for(const item of result.tool_results||[]){if(toolTitles[item.tool]){const card=panel(toolTitles[item.tool]);card.append(fields(item.result));box.append(card);}}
for(const draft of result.action_requests||[]){
const card=panel('Accept or decline this action request');
card.append(fields(draft.proposal),element('p',draft.detail,'muted'));
const choices=element('div',undefined,'action-choices');
const accept=element('button','Accept'),decline=element('button','Decline');
accept.disabled=!draft.submission_available||Boolean(draft.decision);decline.disabled=Boolean(draft.decision);
const status=element('p',draft.decision_status||(draft.decision?'The decision was interrupted. Check M4 or prepare a new draft before continuing.':'Waiting for your decision.'),'muted');status.setAttribute('role','status');
if(draft.accepted_request_id)card.append(fields({request_id:draft.accepted_request_id}));
const decide=async(choice)=>{
accept.disabled=true;decline.disabled=true;status.textContent=choice==='submit'?'Sending your accepted request for approval…':'Declining this request…';
draft.decision='pending';draft.decision_status='The decision was interrupted. Check M4 or prepare a new draft before continuing.';onActionChange();
try{
const response=await api(`/internal/copilot/actions/${encodeURIComponent(draft.draft_id)}/${choice}`,choice==='submit'?{confirmed:true}:{});
status.textContent=choice==='decline'?'Declined. Nothing was sent to M4 or executed.':'Accepted and sent to M4 for approval. Execution and recovery still need confirmation.';
draft.decision=choice==='decline'?'declined':'accepted';
if(choice==='submit'&&response.m4_response?.request_id){draft.accepted_request_id=response.m4_response.request_id;card.append(fields({request_id:draft.accepted_request_id}));}
}catch(error){draft.decision='uncertain';status.textContent=error.message+' Prepare a new draft or check M4 before trying again.';}
draft.decision_status=status.textContent;onActionChange();
};
accept.addEventListener('click',()=>decide('submit'));decline.addEventListener('click',()=>decide('decline'));
choices.append(accept,decline);card.append(choices,status);box.append(card);
}
if(result.citations.length){const sources=element('details');sources.append(element('summary',`Sources (${result.citations.length})`));for(const citation of result.citations){const card=panel(`Source: ${citation.incident_id}`);card.append(fields({source:citation.source,field:citation.field,recorded_value:citation.value}));sources.append(card);}box.append(sources);}return box;}
