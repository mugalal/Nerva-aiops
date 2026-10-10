import {element, panel, fields, empty} from '../components/render.js';
export function renderIncident(bundle, config, onApproval, onStore) {
  const root=document.querySelector('#incident-body');root.replaceChildren();
  const provenance=panel('Evidence source');provenance.append(fields({source:bundle.source||'not recorded',providers:bundle.provider_modes,actuator:bundle.remediation_backend}));root.append(provenance);
  const timeline=panel('Incident timeline'),list=element('ol',undefined,'timeline');
  const events=[[bundle.deployment_event?.timestamp,'Deployment recorded'],[bundle.incident?.started_at,'Incident started'],[bundle.action_result?.started_at,'Action started'],[bundle.action_result?.completed_at,'Action completed'],...(bundle.audit||[]).map(item=>[item.timestamp,item.event||item.event_type||'Recorded workflow event'])];
  for(const [time,title] of events.filter(item=>item[0]).sort((a,b)=>Date.parse(a[0])-Date.parse(b[0]))){const li=element('li');li.append(element('time',time),element('span',title));list.append(li);}
  timeline.append(list.childNodes.length?list:empty('No timeline timestamps recorded.'));root.append(timeline);
  const incident=panel('Incident');incident.append(fields(bundle.incident));root.append(incident);
  const rca=panel('Root cause and evidence');rca.append(fields(bundle.rca ? {root_cause:bundle.rca.root_cause,affected_component:bundle.rca.affected_component,confidence:bundle.rca.confidence}:null));
  if(bundle.rca?.evidence?.length){const evidence=element('ul',undefined,'evidence');for(const item of bundle.rca.evidence)evidence.append(element('li',item));rca.append(evidence);}root.append(rca);
  const decision=panel('Recommended action');decision.append(fields(bundle.decision));
  const actions=element('div',undefined,'actions');for(const action of ['approve','reject']){const button=element('button',action==='approve'?'Approve':'Reject');button.disabled=!config.approval_enabled||bundle.incident?.status!=='AWAITING_APPROVAL'||!bundle.decision||Boolean(bundle.action_result);button.addEventListener('click',()=>{for(const choice of actions.childNodes)choice.disabled=true;onApproval(action,button);});actions.append(button);}decision.append(actions);if(!config.approval_enabled)decision.append(element('p','Approval requires the connected operator configuration.','muted'));root.append(decision);
  const execution=panel('Execution result');execution.append(fields(bundle.action_result));root.append(execution);
  const recovery=panel('Recovery validation');recovery.append(fields(bundle.recovery_result),fields(bundle.recovery_validation));root.append(recovery);
  const workflow=panel('Workflow status');workflow.append(fields(bundle.workflow));root.append(workflow);
  const memory=panel('Incident memory');memory.append(fields(bundle.memory),fields(bundle.archive||bundle.memory_archive||{archive_status:bundle.workflow?.archive_status}));
  if(config.source==='mock'){const button=element('button','Store terminal demo incident');button.disabled=!['RESOLVED','ESCALATED'].includes(bundle.incident?.status)||!bundle.memory||Boolean(bundle.archive);button.addEventListener('click',()=>onStore(button));memory.append(button);}
  else memory.append(element('p','M4 archives terminal incidents automatically. Refresh to check the archive status.','muted'));
  root.append(memory);
  if(bundle.status==='degraded'||bundle.reason||bundle.errors?.length){const state=panel('Unavailable evidence');state.append(fields({reason:bundle.reason,errors:bundle.errors}));root.append(state);}
}
export function renderFinops(bundle) {
  const root=document.querySelector('#finops-body');root.replaceChildren();
  const observed=panel('Resource context');observed.append(fields(bundle.finops_context));root.append(observed);
  const impact=panel('Cost / SLO impact');impact.append(fields({cost_impact:bundle.cost_impact,slo_impact:bundle.slo_impact}));impact.append(element('p','Estimated cost options are not measured financial savings.','muted'));root.append(impact);
}
