const $ = (selector) => document.querySelector(selector);
const stages = ['ingest', 'plan', 'security', 'reliability', 'aggregate', 'blind_critic', 'rework', 'patch_proposal', 'validate', 'finalize'];
const exampleDiff = `diff --git a/src/runner.py b/src/runner.py
--- a/src/runner.py
+++ b/src/runner.py
@@ -1,0 +1,2 @@
+import subprocess
+subprocess.run(command, shell=True)
`;

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (className) node.className = className; return node; }

function renderBudget(budget) {
  const calls = Number(budget?.used_model_calls || 0);
  const maxCalls = Math.max(Number(budget?.max_model_calls || 0), calls, 1);
  const seconds = Number(budget?.used_seconds || 0);
  const maxSeconds = Math.max(Number(budget?.max_seconds || 0), seconds, 1);
  const callsBar = $('#budget-calls');
  const secondsBar = $('#budget-seconds');
  callsBar.max = maxCalls; callsBar.value = calls;
  secondsBar.max = maxSeconds; secondsBar.value = seconds;
  $('#budget-calls-label').textContent = `${calls} / ${maxCalls}`;
  $('#budget-seconds-label').textContent = `${seconds.toFixed(3)}s / ${maxSeconds}s`;
  $('#budget').textContent = JSON.stringify(budget || {}, null, 2);
}

async function showRun(id) {
  const response = await fetch(`/api/v1/runs/${encodeURIComponent(id)}`);
  const run = await response.json();
  $('#selected-run').textContent = id;
  $('#run-summary').textContent = `${run.status} · ${run.repository} · risk=${run.risk_level}`;
  clear($('#dag'));
  const nodeMap = new Map((run.nodes || []).map((node) => [node.node, node]));
  stages.forEach((stage) => {
    const node = nodeMap.get(stage) || (stage === 'rework' ? [...nodeMap.values()].find((item) => item.node.startsWith('rework_')) : undefined);
    const chip = el('span', `${stage}${node ? ` · ${node.status} · #${node.attempt}` : ''}`, `dag-node ${node?.status || 'pending'}`);
    $('#dag').appendChild(chip);
  });
  clear($('#findings'));
  const findings = run.report?.findings || [];
  if (!findings.length) $('#findings').appendChild(el('p', 'Clean / 暂无被 Critic 接受的 Finding。', 'muted'));
  findings.forEach((finding) => {
    const card = el('article', undefined, `finding ${finding.severity || 'low'}`);
    card.append(el('strong', `${finding.type || finding.kind} · ${finding.severity}`));
    card.append(el('div', `${finding.file}:${finding.line ?? '-'} · confidence=${finding.confidence}`));
    card.append(el('p', finding.explanation));
    card.append(el('code', finding.evidence));
    $('#findings').appendChild(card);
  });
  renderBudget(run.budget || {});
  $('#patch').textContent = run.report?.patch ? `${run.report.patch.patch}\n\n${JSON.stringify(run.report.patch.validation, null, 2)}` : '没有生成可安全自动化的补丁。';
  clear($('#trace'));
  (run.trace || []).forEach((event) => {
    const row = el('div', undefined, 'trace-row');
    row.append(el('strong', event.event_type));
    row.append(el('span', `${event.node || '-'} · ${new Date(event.created_at).toLocaleTimeString()}`));
    $('#trace').appendChild(row);
  });
}

async function loadRuns() {
  const response = await fetch('/api/v1/runs?limit=20');
  const data = await response.json();
  $('#run-count').textContent = data.length;
  $('#active-count').textContent = data.filter((item) => ['queued', 'running'].includes(item.status)).length;
  $('#latest-risk').textContent = data[0]?.risk_level || '-';
  clear($('#runs'));
  data.forEach((item) => {
    const row = document.createElement('tr');
    const link = el('a', item.id.slice(0, 8));
    link.href = '#';
    link.addEventListener('click', (event) => { event.preventDefault(); showRun(item.id); });
    const runCell = document.createElement('td'); runCell.appendChild(link); row.appendChild(runCell);
    [item.repository, item.status, item.risk_level, new Date(item.created_at).toLocaleString()].forEach((value) => row.appendChild(el('td', value)));
    $('#runs').appendChild(row);
  });
}

function renderMetrics(metrics, target) {
  clear(target);
  Object.entries(metrics || {}).filter(([key]) => !['model_calls'].includes(key)).forEach(([key, value]) => {
    const card = el('div', undefined, 'metric'); card.append(el('span', key)); card.append(el('strong', value)); target.appendChild(card);
  });
}

async function runBenchmark() {
  const response = await fetch('/api/v1/benchmarks/evaluate', { method: 'POST' });
  const result = await response.json();
  renderMetrics(result.metrics, $('#benchmark-metrics'));
  $('#benchmark-result').textContent = JSON.stringify({ validation: result.validation, holdout: result.holdout }, null, 2);
}

async function loadEvolution() {
  const response = await fetch('/api/v1/evolution');
  const versions = await response.json();
  clear($('#evolution'));
  $('#evolution-version').textContent = versions.find((item) => item.status === 'active')?.version || '-';
  versions.forEach((item) => {
    const row = document.createElement('tr');
    [item.version, item.status, item.gate_decision, item.validation?.passed, item.holdout?.passed].forEach((value) => row.appendChild(el('td', value ?? '-')));
    $('#evolution').appendChild(row);
  });
}

$('#refresh').addEventListener('click', loadRuns);
$('#refresh-evolution').addEventListener('click', loadEvolution);
$('#benchmark').addEventListener('click', runBenchmark);
$('#load-example').addEventListener('click', () => { $('#run-form [name=repository]').value = 'acme/payment-service'; $('#run-form [name=pr_number]').value = 42; $('#run-form [name=diff]').value = exampleDiff; });
$('#run-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const body = { repository: form.get('repository'), pr_number: form.get('pr_number') ? Number(form.get('pr_number')) : null, diff: form.get('diff') };
  const response = await fetch('/api/v1/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  $('#form-result').textContent = JSON.stringify(await response.json(), null, 2);
  loadRuns();
});

loadRuns();
loadEvolution();
setInterval(loadRuns, 5000);
