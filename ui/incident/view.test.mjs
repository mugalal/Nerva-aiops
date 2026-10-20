import test from 'node:test';
import assert from 'node:assert/strict';
import {renderIncident, renderFinops, lifecycle} from './view.js';
import {sortIncidents} from '../overview/view.js';
import {renderAnswer, answerLabel} from '../copilot/view.js';

class Node {
  constructor(tag){this.tag=tag;this.childNodes=[];this.textContent='';this.listeners={};this.classList={add(){}};}
  append(...nodes){this.childNodes.push(...nodes);}
  replaceChildren(...nodes){this.childNodes=nodes;}
  addEventListener(name, listener){this.listeners[name]=listener;}
  setAttribute(){}
}
const roots = {'#incident-body':new Node('main'),'#finops-body':new Node('main')};
globalThis.document = {createElement:tag=>new Node(tag), querySelector:selector=>roots[selector]};
function text(node){return [node.textContent,...node.childNodes.map(text)].join(' ');}
function find(node, tag){return [node,...node.childNodes.flatMap(child=>find(child,tag))].filter(child=>child.tag===tag);}
const base = {source:'mixed',incident:{incident_id:'INC-1',status:'AWAITING_APPROVAL',started_at:'2026-10-10T08:00:00Z'},
  decision:{recommended_action:'SCALE',parameters:{replicas:3}},rca:{root_cause:'traffic_spike',evidence:['measured CPU']},
  action_result:null,recovery_result:null,finops_context:null,proposal_digest:'d'.repeat(64),
  proposal_state:{created_at:'2026-10-10T08:01:00Z',expires_at:'2026-10-10T08:06:00Z',expired:false}};

test('approval buttons require a pending proposal and both are locked during a decision',()=>{
  let choice;
  renderIncident(base,{approval_enabled:true},value=>choice=value,()=>{});
  const buttons=find(roots['#incident-body'],'button');
  assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,false);
  buttons[1].listeners.click();assert.equal(choice,'reject');
  assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].disabled,true);
  for(const status of ['EXECUTING','VALIDATING','RESOLVED','ESCALATED']){
    renderIncident({...base,incident:{...base.incident,status}},{approval_enabled:true},()=>{},()=>{});
    assert.equal(find(roots['#incident-body'],'button')[0].disabled,true);
  }
});
test('missing recovery and FinOps display absence instead of success or savings',()=>{
  renderIncident(base,{approval_enabled:false},()=>{},()=>{});renderFinops(base);
  assert.match(text(roots['#incident-body']),/No recorded data/);
  assert.doesNotMatch(text(roots['#incident-body']),/recovered: true/);
  assert.match(text(roots['#finops-body']),/not measured financial savings/);
  assert.doesNotMatch(text(roots['#finops-body']),/100%|\$\d/);
});
test('timeline, archive and citations render exact recorded values as text',()=>{
  renderIncident({...base,rca:{root_cause:'<script>unsafe</script>',evidence:['raw <b>evidence</b>']},
    workflow:{archive_status:'pending',recovery_deadline_at:'2026-10-10T08:06:00Z'},
    archive:{stored_at:'2026-10-10T08:10:00Z',resolved:false},audit:[{timestamp:'2026-10-10T08:00:05Z',event_type:'MANUAL_APPROVAL_REJECTED'}]},
    {approval_enabled:false},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/MANUAL_APPROVAL_REJECTED/);
  assert.match(text(roots['#incident-body']),/pending/);
  assert.match(text(roots['#incident-body']),/2026-10-10T08:06:00Z/);
  assert.match(text(roots['#incident-body']),/<script>unsafe<\/script>/);
  assert.equal(find(roots['#incident-body'],'script').length,0);
  const answer=renderAnswer({answer:'Recorded recovery failed',mode:'offline_fallback',citations:[
    {incident_id:'INC-1',source:'real',field:'context.recovery_result.recovered',value:false}]});
  assert.match(text(answer),/context.recovery_result.recovered/);assert.match(text(answer),/false/);
});

test('approve sends the reviewed digest; expired or digest-less proposals cannot be approved',()=>{
  let sent;
  renderIncident(base,{approval_enabled:true},(action,button,review)=>sent={action,review},()=>{});
  find(roots['#incident-body'],'button')[0].listeners.click();
  assert.equal(sent.action,'approve');assert.equal(sent.review.digest,'d'.repeat(64));
  renderIncident({...base,proposal_state:{...base.proposal_state,expired:true}},{approval_enabled:true},()=>{},()=>{});
  let [approve,reject]=find(roots['#incident-body'],'button');
  assert.equal(approve.disabled,true);assert.equal(reject.disabled,false);
  assert.match(text(roots['#incident-body']),/proposal has expired/);
  renderIncident({...base,proposal_digest:null},{approval_enabled:true},()=>{},()=>{});
  assert.equal(find(roots['#incident-body'],'button')[0].disabled,true);
});
test('token mode asks for the operator token and passes it only with the decision',()=>{
  let sent;
  renderIncident(base,{approval_enabled:true,operator_auth:'token'},(action,button,review)=>sent=review,()=>{});
  const [input]=find(roots['#incident-body'],'input');
  assert.equal(input.type,'password');input.value='typed-secret';
  find(roots['#incident-body'],'button')[0].listeners.click();
  assert.equal(sent.token,'typed-secret');
  renderIncident(base,{approval_enabled:false,operator_auth:'disabled'},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/operator boundary is not configured/);
});
test('provenance, missing evidence and operator observations are explicit',()=>{
  renderIncident({...base,source:'mixed',provenance:{used:{rca:['real'],remediation:['mock']},unused:['finops'],modes:{rca:'real',remediation:'mock'}},
    missing:['recovery_result','evidence'],incident:{...base.incident,status:'VALIDATING'},
    workflow:{execution_intent:{action_id:'ACT-1'},reconciliation:{outcome:'CONFIRMED',observed_by:'alice',evidence:'kubectl rollout status'},
      recovery_deadline:{at:'2026-10-10T08:12:00Z',seconds_remaining:120,expired:false}}},
    {approval_enabled:true},()=>{},()=>{});
  const shown=text(roots['#incident-body']);
  assert.match(shown,/never stored as real history/);
  assert.match(shown,/Measured recovery: not recorded/);assert.match(shown,/Captured evidence: not recorded/);
  assert.match(shown,/Operator observation \(not an actuator result\)/);assert.match(shown,/alice/);
  assert.match(shown,/2026-10-10T08:12:00Z/);
  assert.match(shown,/No measured M1 recovery is recorded/);
  for(const button of find(roots['#incident-body'],'button').slice(0,2))assert.equal(button.disabled,true);
});

test('external-model answers are labelled apart from deterministic grounded answers',()=>{
  assert.match(answerLabel({mode:'ai_chat',source:'real'}),/AI-generated \(external model\)/);
  assert.match(answerLabel({mode:'ai_chat',kind:'general_guidance'}),/not incident evidence/);
  assert.equal(answerLabel({mode:'deterministic_grounded'}),'Deterministic recorded answer');
  assert.equal(answerLabel({mode:'offline_fallback'}),'Deterministic recorded answer');
  const box=renderAnswer({mode:'deterministic_grounded',status:'insufficient_evidence',answer:'NEXUS does not have enough recorded evidence.',citations:[]});
  assert.match(text(box),/nothing was inferred/);
});

test('lifecycle never marks unrecorded action, recovery or archive as done',()=>{
  const stages=Object.fromEntries(lifecycle(base).map(([name,state,detail])=>[name,{state,detail}]));
  assert.equal(stages.Decide.state,'current');assert.equal(stages.Act.state,'missing');
  assert.equal(stages.Recover.state,'missing');assert.equal(stages.Recover.detail,'Not measured');assert.equal(stages.Learn.state,'missing');
  const failed=Object.fromEntries(lifecycle({...base,incident:{...base.incident,status:'RESOLVED'},action_result:{status:'FAILED'},recovery_result:{recovered:false}}).map(([n,s])=>[n,s]));
  assert.equal(failed.Act,'failed');assert.equal(failed.Recover,'failed');
  assert.equal(Object.fromEntries(lifecycle({...base,workflow:{execution_ambiguous:true}}).map(([n,s])=>[n,s])).Act,'failed');
});
test('incident summary states the next step and the Copilot hand-off is a link, not a decision button',()=>{
  let asked=false;
  renderIncident(base,{approval_enabled:true},()=>{},()=>{},()=>asked=true);
  assert.match(text(roots['#incident-body']),/waiting for your decision/);
  const [link]=find(roots['#incident-body'],'a');link.listeners.click({preventDefault(){}});assert.equal(asked,true);
  renderIncident({...base,incident:{...base.incident,status:'RESOLVED'}},{approval_enabled:true},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/no successful recovery measurement is recorded/);
});
test('overview puts incidents awaiting a decision first, then open, then resolved',()=>{
  const rows=[{incident_id:'R',status:'RESOLVED',started_at:'2026-10-10T09:00:00Z'},{incident_id:'O',status:'VALIDATING',started_at:'2026-10-10T07:00:00Z'},
    {incident_id:'A',status:'AWAITING_APPROVAL',started_at:'2026-10-10T06:00:00Z'},{incident_id:'O2',status:'DETECTED',started_at:'2026-10-10T08:00:00Z'}];
  assert.deepEqual(sortIncidents(rows).map(r=>r.incident_id),['A','O2','O','R']);
});
test('proof of fix and KPI cards show only recorded measurements, never illustrative numbers',()=>{
  const recorded={...base,incident:{...base.incident,status:'RESOLVED'},decision:{recommended_action:'SCALE',parameters:{replicas:4,direction:'down'}},
    evidence:{captured_at:'2026-10-10T08:01:00Z',before:{version:'v1',metrics:{replica_count:7,request_rate:11.2,latency_p95_ms:9.4,http_5xx_rate:0,cpu:0.05}},
      baseline:{thresholds:{latency_p95_ms_max:25,http_5xx_rate_max:0.01},request_rate:{average:10}}},
    recovery_result:{recovered:true,slo_restored:true,recovery_time_seconds:75,before:{latency_p95_ms:9.4,http_5xx_rate:0},after:{latency_p95_ms:10.2,http_5xx_rate:0}}};
  renderIncident(recorded,{approval_enabled:false},()=>{},()=>{});
  const shown=text(roots['#incident-body']);
  assert.match(shown,/Replicas: 7/);assert.match(shown,/Replicas: 4/);assert.match(shown,/10\.2 ms/);assert.match(shown,/11\.2 req\/s/);
  assert.match(shown,/Healthy limit: 25 ms \(within\)/);
  assert.doesNotMatch(shown,/160 RPS|62%|SLO Passed|Zero Errors|Spike Active/);
  renderIncident({...recorded,evidence:{before:{metrics:{}}}},{approval_enabled:false},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/not recorded/);
});
test('safety controls, the change in words, registry limits and the audit chain are visible',()=>{
  const bundle={...base,incident:{...base.incident,status:'ESCALATED'},
    decision:{recommended_action:'SCALE',target:'payment-service',parameters:{replicas:4,direction:'down'},reason:'Capacity right-sizing: measured'},
    evidence:{before:{metrics:{replica_count:7}}},
    workflow:{kind:'rightsizing',coalesced_incident_ids:['INC-B'],recurrences_in_cooldown:2,withdrawn:'proposal expired before approval'},
    service_policy:{service:'payment-service',min_replicas:1,max_replicas:10,max_scale_step:7,max_scale_down_step:3,depends_on:[]},
    audit_chain:{valid:true,chained:6,legacy_unchained:0,head_hash:'abcdef0123456789',problems:0},
    audit:[{timestamp:'2026-10-10T08:00:30Z',event_type:'INCIDENT_COALESCED'}]};
  renderIncident(bundle,{approval_enabled:false,source:'real'},()=>{},()=>{});
  const shown=text(roots['#incident-body']);
  assert.match(shown,/Scale down payment-service from 7 to 4 replicas/);
  assert.match(shown,/Merged 1 repeat detection into this incident.*INC-B/);
  assert.match(shown,/alerted 2 more times during the post-incident cooldown/);
  assert.match(shown,/escalated: the proposal was withdrawn \(proposal expired before approval\)/);
  assert.match(shown,/Registry limits for payment-service: 1-10 replicas/);
  assert.match(shown,/Repeat detection merged into this incident \(INCIDENT_COALESCED\)/);
  assert.match(shown,/T\+30s/);
  assert.match(shown,/Hash chain verified: 6 chained records/);
  renderIncident({...bundle,audit_chain:{valid:false,problems:1}},{approval_enabled:false,source:'real'},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/Hash chain check FAILED: 1 problem/);
});
test('a dependency block links to the root incident and the topology shows callers before dependencies',async()=>{
  renderIncident({...base,incident:{...base.incident,status:'ESCALATED'},workflow:{blocked_by:{dependency:'payment-service',incident_id:'INC-PAY'}}},{approval_enabled:false},()=>{},()=>{});
  assert.match(text(roots['#incident-body']),/Blocked by dependency: payment-service has open incident INC-PAY/);
  assert.equal(find(roots['#incident-body'],'a').find(a=>a.textContent.includes('root incident')).href,'#incident/INC-PAY');
  const {renderTopology}=await import('../overview/view.js');
  roots['#topology']=new Node('div');let opened;
  renderTopology([{name:'payment-service',depends_on:[],open_incidents:[{incident_id:'INC-PAY',status:'AWAITING_APPROVAL',kind:'incident'}]},
    {name:'checkout-service',depends_on:['payment-service'],open_incidents:[],latest_dependency_block:{dependency:'payment-service'}}],id=>opened=id);
  const nodes=find(roots['#topology'],'button');
  assert.deepEqual(nodes.map(n=>n.childNodes[0].textContent),['checkout-service','payment-service']);
  assert.match(text(roots['#topology']),/No open incident/);assert.doesNotMatch(text(roots['#topology']),/healthy/i);
  assert.equal(nodes[0].disabled,true);nodes[1].listeners.click();assert.equal(opened,'INC-PAY');
});
