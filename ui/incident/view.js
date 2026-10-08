import {element, panel, fields, empty} from '../components/render.js';
export function renderIncident(bundle, config, onApproval, onStore) {
  const root=document.querySelector('#incident-body');root.replaceChildren();
  const timeline=panel('Incident timeline'),list=element('ol',undefined,'timeline');
  for(const [time,title] of [[bundle.deployment_event?.timestamp,'Deployment recorded'],[bundle.incident?.started_at,'Incident started'],[bundle.action_result?.started_at,'Action started'],[bundle.action_result?.completed_at,'Action completed']]){if(time){const li=element('li');li.append(element('time',time),element('span',title));list.append(li);}}
  timeline.append(list.childNodes.length?list:empty('No timeline timestamps recorded.'));root.append(timeline);
  const incident=panel('Incident');incident.append(fields(bundle.incident));root.append(incident);
  const rca=panel('Root cause and evidence');rca.append(fields(bundle.rca ? {root_cause:bundle.rca.root_cause,affected_component:bundle.rca.affected_component,confidence:bundle.rca.confidence}:null));
  if(bundle.rca?.evidence?.length){const evidence=element('ul',undefined,'evidence');for(const item of bundle.rca.evidence)evidence.append(element('li',item));rca.append(evidence);}root.append(rca);
  const decision=panel('Recommended action');decision.append(fields(bundle.decision));
  const actions=element('div',undefined,'actions');for(const action of ['approve','reject']){const button=element('button',action==='approve'?'Approve':'Reject');button.disabled=!config.approval_enabled||!bundle.decision||Boolean(bundle.action_result);button.addEventListener('click',()=>onApproval(action,button));actions.append(button);}decision.append(actions);if(!config.approval_enabled)decision.append(element('p','Approval is unavailable in this mode or has not been connected.','muted'));root.append(decision);
  const execution=panel('Execution result');execution.append(fields(bundle.action_result));root.append(execution);
  const recovery=panel('Recovery validation');recovery.append(fields(bundle.recovery_result));root.append(recovery);
  const memory=panel('Incident memory');memory.append(fields(bundle.memory));const button=element('button','Store resolved incident');button.disabled=bundle.incident?.status!=='RESOLVED'||!bundle.memory;button.addEventListener('click',()=>onStore(button));memory.append(button);root.append(memory);
}
export function renderFinops(bundle) {
  const root=document.querySelector('#finops-body');root.replaceChildren();
  const observed=panel('Resource context');observed.append(fields(bundle.finops_context));root.append(observed);
  const impact=panel('Cost / SLO impact');impact.append(fields({cost_impact:bundle.cost_impact,slo_impact:bundle.slo_impact}));impact.append(element('p','Estimated cost options are not measured financial savings.','muted'));root.append(impact);
}
