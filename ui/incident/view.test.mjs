import test from 'node:test';
import assert from 'node:assert/strict';
import {renderIncident, renderFinops} from './view.js';
import {renderAnswer} from '../copilot/view.js';

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
  action_result:null,recovery_result:null,finops_context:null};

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
