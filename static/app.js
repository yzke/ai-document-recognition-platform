let currentJob = null, currentPage = null, currentStage = 'input', lastJob = null, timer = null, pageFilter = 'all', vlmView = 'pretty';
let selectedPages = new Set(), zoom = 1, rotation = 0, panX = 0, panY = 0, isPanning = false, panStart = {x: 0, y: 0};

const PIPELINE_STAGES = [
  {key: 'input', no: '01', title: '文档输入', tech: 'PyMuPDF'},
  {key: 'ocr', no: '02', title: 'OCR 提取', tech: 'RapidOCR'},
  {key: 'vlm', no: '03', title: 'VLM 理解', tech: 'Qwen3-VL'},
  {key: 'extract', no: '04', title: '结构化提取', tech: 'JSON Schema'},
  {key: 'rules', no: '05', title: '规则校验', tech: '硬规则引擎'},
  {key: 'routing', no: '06', title: '置信度路由', tech: '自动分流'},
  {key: 'review', no: '07', title: '人工复核', tech: '审计日志'}
];

const $ = id => document.getElementById(id);
const form = $('form'), pages = $('pages'), textBox = $('textBox'), pageImage = $('pageImage'), imageBox = $('imageBox');

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
function formatDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60, h = Math.floor(m / 60);
  return h ? `${h}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
function showMessage(msg) {$('jobline').textContent = msg;}
function closeDrawers() {$('settingsDrawer').classList.add('drawer-closed'); $('historyDrawer').classList.add('drawer-closed'); $('drawerMask').classList.remove('open');}
function openSettings() {closeDrawers(); $('settingsDrawer').classList.remove('drawer-closed'); $('drawerMask').classList.add('open');}
function openHistory() {closeDrawers(); $('historyDrawer').classList.remove('drawer-closed'); $('drawerMask').classList.add('open'); loadHistory();}

$('openSettings').onclick = openSettings;
$('closeSettings').onclick = closeDrawers;
$('openHistory').onclick = openHistory;
$('closeHistory').onclick = closeDrawers;
$('drawerMask').onclick = closeDrawers;
$('refreshHistory').onclick = loadHistory;
$('runVlm').onclick = () => startVlm();
$('rerunOcr').onclick = () => rerunCurrentOcr();
$('filterAll').onclick = () => setPageFilter('all');
$('filterCand').onclick = () => setPageFilter('candidate');
$('filterVlm').onclick = () => setPageFilter('vlm');
$('filterNoVlm').onclick = () => setPageFilter('noVlm');
$('pageSearch').oninput = () => renderPages();
$('clearFilter').onclick = () => {$('pageSearch').value = ''; setPageFilter('all');};
$('selectVisible').onclick = () => {for (const p of filteredResults()) selectedPages.add(p.page); renderPages();};
$('selectCandidates').onclick = () => {for (const p of (lastJob?.page_results || [])) if (p.candidate) selectedPages.add(p.page); renderPages();};
$('clearSelected').onclick = () => {selectedPages.clear(); renderPages();};
$('zoomOut').onclick = e => {e.preventDefault(); zoom = Math.max(.25, Math.round((zoom - .15) * 100) / 100); applyImageTransform();};
$('zoomIn').onclick = e => {e.preventDefault(); zoom = Math.min(4, Math.round((zoom + .15) * 100) / 100); applyImageTransform();};
$('rotateLeft').onclick = e => {e.preventDefault(); rotation = (rotation - 90 + 360) % 360; applyImageTransform();};
$('rotateRight').onclick = e => {e.preventDefault(); rotation = (rotation + 90) % 360; applyImageTransform();};
$('resetView').onclick = e => {e.preventDefault(); fitPage();};

form.onsubmit = async e => {
  e.preventDefault();
  $('submit').disabled = true;
  pages.innerHTML = '';
  textBox.innerHTML = '<pre>正在上传...</pre>';
  const r = await fetch('/api/jobs', {method: 'POST', body: new FormData(form)});
  const d = await r.json();
  if (!r.ok) {
    textBox.innerHTML = `<pre>${escapeHtml(d.error || '上传失败')}</pre>`;
    $('submit').disabled = false;
    return;
  }
  currentJob = d.job_id;
  currentPage = null;
  currentStage = 'input';
  selectedPages.clear();
  $('stop').disabled = false;
  $('download').style.display = 'none';
  if (timer) clearInterval(timer);
  timer = setInterval(poll, 1200);
  await poll();
  loadHistory();
  loadKeywordHistory();
};
$('stop').onclick = async () => {
  if (!currentJob) return;
  await fetch(`/api/jobs/${currentJob}/stop`, {method: 'POST'});
  $('stop').disabled = true;
};

pageImage.onload = () => fitPage();
pageImage.ondragstart = e => e.preventDefault();
imageBox.onpointerdown = e => {
  if (!currentPage || (e.pointerType === 'mouse' && e.button !== 0)) return;
  e.preventDefault();
  isPanning = true;
  panStart = {x: e.clientX - panX, y: e.clientY - panY};
  if (imageBox.setPointerCapture) imageBox.setPointerCapture(e.pointerId);
  imageBox.classList.add('panning');
};
imageBox.onpointermove = e => {
  if (!isPanning) return;
  e.preventDefault();
  panX = e.clientX - panStart.x;
  panY = e.clientY - panStart.y;
  applyImageTransform();
};
function stopPanning(e) {
  if (e && imageBox.hasPointerCapture && imageBox.hasPointerCapture(e.pointerId)) imageBox.releasePointerCapture(e.pointerId);
  isPanning = false;
  imageBox.classList.remove('panning');
}
imageBox.onpointerup = stopPanning;
imageBox.onpointercancel = stopPanning;
imageBox.onlostpointercapture = () => {isPanning = false; imageBox.classList.remove('panning');};

function fitPage() {
  if (!pageImage.naturalWidth || !pageImage.naturalHeight) return;
  const sx = (imageBox.clientWidth - 24) / pageImage.naturalWidth, sy = (imageBox.clientHeight - 36) / pageImage.naturalHeight;
  zoom = Math.max(.2, Math.min(1, sx, sy));
  panX = 0; panY = 0; rotation = 0;
  applyImageTransform();
}
function applyImageTransform() {
  pageImage.style.transform = `translate(${panX}px, ${panY}px) rotate(${rotation}deg) scale(${zoom})`;
  $('zoomLabel').textContent = `${Math.round(zoom * 100)}% · ${rotation}°`;
}
window.addEventListener('resize', () => {if (currentPage && rotation === 0) fitPage();});

function setStage(stage) {
  currentStage = stage;
  renderPipeline();
  loadCurrent();
}
function setPageFilter(f) {
  pageFilter = f;
  for (const [id, val] of [['filterAll', 'all'], ['filterCand', 'candidate'], ['filterVlm', 'vlm'], ['filterNoVlm', 'noVlm']]) $(id).classList.toggle('active', f === val);
  renderPages();
}
function activeVlmCount(job) {return Object.values(job?.vlm_results || {}).filter(v => ['queued', 'running'].includes(v.status)).length;}
function pageMeta(pageNo) {return (lastJob?.page_results || []).find(p => p.page === pageNo) || null;}
function vlmMeta(pageNo) {return (lastJob?.vlm_results || {})[String(pageNo)] || null;}
function extractMeta(pageNo) {return (lastJob?.extracted_results || {})[String(pageNo)] || null;}
function scoreText(score) {return typeof score === 'number' && score > 0 ? `${Math.round(score * 100)}%` : '-';}
function confidenceText(v) {return typeof v === 'number' ? `${Math.round(v * 100)}%` : '-';}
function routingText(r) {return ({auto_approve: '自动通过', human_review: '等待人工审核', rejected: '拒绝/标红'}[r] || r || '-');}
function fieldLabel(k) {
  return ({
    invoice_number: '发票号', date: '日期', amount_excl_tax: '不含税额', tax_amount: '税额', total_amount: '价税合计',
    seller_name: '销售方', seller_tax_id: '销售方税号', buyer_name: '购买方', buyer_tax_id: '购买方税号',
    contract_number: '合同编号', project_name: '项目名称', party_a: '甲方', party_b: '乙方', amount: '金额', signature_status: '签字盖章'
  }[k] || k);
}
function ocrSummary(p) {
  if (!p) return '';
  const low = p.low_conf_count || 0, avg = scoreText(p.avg_score), min = scoreText(p.min_score);
  return `<div class="${low ? 'review-alert compact' : 'review-ok compact'}"><b>${low ? '存在低置信 OCR' : 'OCR 置信度'}</b><span>平均 ${avg} · 最低 ${min} · 低置信块 ${low}</span>${p.ocr_reordered ? '<span>已启用坐标重排</span>' : ''}</div>`;
}
function routingChip(x) {
  if (!x) return '';
  const map = {auto_approve: ['ok', '自动通过'], human_review: ['warn', '待审核'], rejected: ['bad', '已拒绝'], failed: ['bad', '提取失败']};
  const row = map[x.status === 'failed' ? 'failed' : x.routing];
  return row ? `<span class="chip ${row[0]}">${row[1]}</span>` : '';
}
function fieldStatusChip(f) {
  if (f.status === 'missing') return '<span class="chip bad">缺失</span>';
  if (f.status === 'manual') return '<span class="chip ok">人工修正</span>';
  if (f.needs_review) return '<span class="chip warn">需复核</span>';
  return '<span class="chip ok">自动</span>';
}

async function loadHistory() {
  const r = await fetch('/api/jobs'), d = await r.json();
  $('historyList').innerHTML = (d.jobs || []).map(j => `<button class="history-item" type="button" data-job="${j.id}"><b>${escapeHtml(j.filename || j.id)}</b><div class="muted small">${escapeHtml(j.status)} · ${j.done_pages}/${j.total_pages || '?'} · 命中 ${j.candidate_count}</div></button>`).join('') || '<div class="empty">暂无历史</div>';
  for (const b of $('historyList').querySelectorAll('button')) b.onclick = () => restoreJob(b.dataset.job);
}
async function loadKeywordHistory() {
  const r = await fetch('/api/keyword-history'), d = await r.json();
  $('keywordHistory').innerHTML = (d.items || []).map(i => `<option value="${escapeHtml(i.keywords)}"></option>`).join('');
}
async function loadLocalSettings() {
  try {
    const r = await fetch('/api/local-settings'), d = await r.json();
    const status = $('localKeyStatus');
    if (status) status.textContent = d.api_key_configured ? `已保存本机 Key · 默认模型 ${d.default_vlm_model}` : `未保存本机 Key · 默认模型 ${d.default_vlm_model}`;
    const select = form.elements.vlm_model;
    if (select && d.default_vlm_model) select.value = d.default_vlm_model;
  } catch (e) {
    const status = $('localKeyStatus');
    if (status) status.textContent = '无法读取本机 Key 状态';
  }
}
async function restoreJob(jobId) {
  currentJob = jobId;
  currentPage = null;
  currentStage = 'input';
  selectedPages.clear();
  $('download').style.display = 'none';
  if (timer) clearInterval(timer);
  timer = setInterval(poll, 1200);
  closeDrawers();
  await poll();
}

function renderQualitySummary(job) {
  const box = $('qualitySummary'), q = job?.quality_summary || {}, routing = q.routing || {}, low = q.low_confidence_fields || {}, rules = q.rule_hits || {};
  const has = routing.auto_approve || routing.human_review || routing.rejected || routing.failed || Object.keys(low).length || Object.keys(rules).length;
  if (!has) {box.style.display = 'none'; box.innerHTML = ''; return;}
  const lowText = Object.entries(low).map(([k, v]) => `${fieldLabel(k)} ${v}`).join(' · ') || '无';
  const ruleText = Object.entries(rules).map(([k, v]) => `${k} ${v}`).join(' · ') || '无';
  box.style.display = 'grid';
  box.innerHTML = `<div><b>路由</b><span>自动 ${routing.auto_approve || 0} · 待审 ${routing.human_review || 0} · 拒绝 ${routing.rejected || 0} · 失败 ${routing.failed || 0}</span></div><div><b>低置信字段</b><span>${escapeHtml(lowText)}</span></div><div><b>规则命中</b><span>${escapeHtml(ruleText)}</span></div>`;
}
async function poll() {
  if (!currentJob) return;
  const r = await fetch(`/api/jobs/${currentJob}`);
  lastJob = await r.json();
  const total = lastJob.total_pages || 0, done = lastJob.done_pages || 0, activeVlm = activeVlmCount(lastJob), summary = lastJob.extraction_summary || {};
  $('fileName').textContent = lastJob.filename || '未上传';
  $('jobStatus').textContent = lastJob.status || '-';
  $('jobProgress').textContent = `${done} / ${total || '?'}`;
  $('candidateCount').textContent = lastJob.candidate_pages?.length || 0;
  $('runtimeInfo').textContent = formatDuration(lastJob.elapsed_seconds);
  $('jobline').textContent = activeVlm ? `AI 提取中，剩余 ${activeVlm} 页` : (summary.auto_approve || summary.human_review || summary.rejected || summary.failed) ? `结构化：自动通过 ${summary.auto_approve || 0} · 待审核 ${summary.human_review || 0} · 拒绝 ${summary.rejected || 0} · 失败 ${summary.failed || 0}` : (lastJob.message || '');
  $('progress').style.width = total ? `${Math.round(done * 100 / total)}%` : '0%';
  $('vlmStatus').textContent = lastJob.vlm_configured ? '大模型已配置' : '大模型未配置，仅支持文本识别';
  renderQualitySummary(lastJob);
  renderPages();
  renderPipeline();
  if (currentPage) refreshCurrentStage();
  const hasExtract = Object.keys(lastJob.extracted_results || {}).length > 0;
  $('exportJson').href = `/api/jobs/${currentJob}/export`;
  $('exportJson').style.display = hasExtract ? 'inline' : 'none';
  if (['done', 'failed', 'stopped'].includes(lastJob.status) && !activeVlm) {
    clearInterval(timer);
    timer = null;
    $('submit').disabled = false;
    $('stop').disabled = true;
    if (lastJob.status === 'done') {
      $('download').href = `/job/${currentJob}/full_text.txt`;
      $('download').style.display = 'inline';
    }
  }
}

function filteredResults() {
  const q = $('pageSearch').value.trim().toLowerCase();
  return (lastJob?.page_results || []).filter(p => {
    const v = vlmMeta(p.page) || {};
    if (pageFilter === 'candidate' && !p.candidate) return false;
    if (pageFilter === 'vlm' && v.status !== 'done') return false;
    if (pageFilter === 'noVlm' && v.status === 'done') return false;
    if (!q) return true;
    return [String(p.page), String(p.chars), ...(p.keywords || [])].join(' ').toLowerCase().includes(q);
  });
}
function renderPages() {
  const rows = filteredResults();
  $('filterSummary').textContent = `显示 ${rows.length} / ${(lastJob?.page_results || []).length} 页 · 已选 ${selectedPages.size}`;
  pages.innerHTML = '';
  if (!lastJob) {pages.innerHTML = '<div class="empty">上传 PDF 后，这里会显示逐页文本识别结果</div>'; return;}
  if (!rows.length) {pages.innerHTML = '<div class="empty">当前筛选条件下没有页面</div>'; return;}
  for (const p of rows) {
    const v = vlmMeta(p.page) || {}, x = extractMeta(p.page) || {};
    const b = document.createElement('button');
    b.className = 'pagebtn' + (p.page === currentPage ? ' active' : '');
    const status = v.status ? `<span class="chip vlm">AI ${v.status}</span>` : '';
    const review = v.review_required ? '<span class="chip bad">需复核</span>' : '';
    const low = (p.low_conf_count || 0) > 0 ? `<span class="chip bad">低置信 ${p.low_conf_count}</span>` : '';
    const stale = v.stale_after_ocr ? '<span class="chip bad">AI 需重跑</span>' : '';
    b.innerHTML = `<input class="pagecheck" type="checkbox" ${selectedPages.has(p.page) ? 'checked' : ''}><div><div>第 ${p.page} 页 · ${p.chars} 字 · ${p.seconds}s</div><div class="chips">${p.candidate ? '<span class="chip hit">命中</span>' : ''}${(p.keywords || []).map(k => `<span class="chip">${escapeHtml(k)}</span>`).join('')}${low}${status}${review}${stale}${routingChip(x)}</div></div>`;
    b.onclick = e => {
      if (e.target.classList.contains('pagecheck')) {
        selectedPages.has(p.page) ? selectedPages.delete(p.page) : selectedPages.add(p.page);
        renderPages();
        return;
      }
      selectPage(p.page);
    };
    pages.appendChild(b);
  }
}

function stageInfo(key) {
  if (!currentJob || !currentPage) return {status: 'pending', label: '待选择'};
  const p = pageMeta(currentPage), v = vlmMeta(currentPage) || {}, x = extractMeta(currentPage) || {};
  const validation = x.validation || {};
  if (key === 'input') return {status: 'done', label: '已载入'};
  if (key === 'ocr') return p ? {status: p.low_conf_count ? 'review' : 'done', label: p.low_conf_count ? '低置信' : '完成'} : {status: 'pending', label: '等待'};
  if (key === 'vlm') {
    if (['queued', 'running'].includes(v.status)) return {status: 'running', label: '处理中'};
    if (v.status === 'done') return {status: v.review_required || v.stale_after_ocr ? 'review' : 'done', label: v.stale_after_ocr ? '需重跑' : '完成'};
    if (v.status === 'failed') return {status: 'failed', label: '失败'};
    return {status: 'pending', label: lastJob?.vlm_configured ? '未执行' : '未配置'};
  }
  if (key === 'extract') {
    if (x.status === 'done' || x.fields) return {status: 'done', label: '已结构化'};
    if (x.status === 'failed') return {status: 'failed', label: '失败'};
    return {status: 'pending', label: '等待'};
  }
  if (key === 'rules') {
    if (!x.fields && x.status !== 'failed') return {status: 'pending', label: '等待'};
    if (x.status === 'failed' || (validation.errors || []).length) return {status: 'failed', label: '有错误'};
    if ((validation.warnings || []).length || (validation.low_confidence_fields || []).length || (validation.rules || []).length) return {status: 'review', label: '有提醒'};
    return {status: 'done', label: '通过'};
  }
  if (key === 'routing') {
    if (!x.routing) return {status: 'pending', label: '等待'};
    if (x.routing === 'auto_approve') return {status: 'done', label: '自动通过'};
    if (x.routing === 'human_review') return {status: 'review', label: '待审核'};
    if (x.routing === 'rejected' || x.status === 'failed') return {status: 'failed', label: '拒绝'};
  }
  if (key === 'review') {
    if (!x.routing) return {status: 'pending', label: '等待'};
    if (x.manual_status === 'approved') return {status: 'done', label: '已通过'};
    if (x.manual_status === 'rejected') return {status: 'failed', label: '已拒绝'};
    if (x.routing === 'human_review') return {status: 'review', label: '待处理'};
    return {status: 'done', label: '无需人工'};
  }
  return {status: 'pending', label: '等待'};
}
function renderPipeline() {
  const box = $('pipeline');
  if (!box) return;
  box.innerHTML = PIPELINE_STAGES.map(s => {
    const info = stageInfo(s.key);
    return `<button class="stagebtn stage-${info.status}${currentStage === s.key ? ' active' : ''}" type="button" data-stage="${s.key}"><span class="stage-no">${s.no}</span><span class="stage-copy"><b>${s.title}</b><small>${s.tech}</small></span><span class="stage-status">${info.label}</span></button>`;
  }).join('');
  for (const b of box.querySelectorAll('[data-stage]')) b.onclick = () => setStage(b.dataset.stage);
}

async function selectPage(p) {
  currentPage = p;
  renderPages();
  renderPipeline();
  pageImage.src = `/job/${currentJob}/image/${p}`;
  $('runVlm').disabled = false;
  $('rerunOcr').disabled = false;
  loadCurrent();
}
async function loadCurrent() {
  if (!currentJob || !currentPage) {
    textBox.innerHTML = '<pre>选择页面查看管道结果</pre>';
    return;
  }
  if (currentStage === 'input') return loadInputStage();
  if (currentStage === 'ocr') return loadOcrStage();
  if (currentStage === 'vlm') return loadVlm();
  if (currentStage === 'extract') return loadExtractStage();
  if (currentStage === 'rules') return loadRulesStage();
  if (currentStage === 'routing') return loadRoutingStage();
  if (currentStage === 'review') return loadStructured();
}
async function refreshCurrentStage() {
  if (!['vlm', 'extract', 'rules', 'routing', 'review'].includes(currentStage)) return;
  await loadCurrent();
}
function loadInputStage() {
  const p = pageMeta(currentPage) || {}, fd = new FormData(form);
  const data = {
    job_id: currentJob,
    filename: lastJob?.filename || '',
    page: currentPage,
    total_pages: lastJob?.total_pages || 0,
    page_range: fd.get('page_range') || '',
    render_dpi: fd.get('dpi') || '',
    page_image: `/job/${currentJob}/image/${currentPage}`
  };
  textBox.innerHTML = `<div class="stage-panel"><div class="stage-head"><div><b>01 文档输入</b><span>PDF 页面已由 PyMuPDF 渲染为图片，供 OCR 和 VLM 复核使用。</span></div><span class="chip ok">已载入</span></div><div class="stage-grid"><div><b>文件</b><span>${escapeHtml(data.filename || '-')}</span></div><div><b>页码</b><span>${currentPage} / ${data.total_pages || '?'}</span></div><div><b>渲染 DPI</b><span>${escapeHtml(data.render_dpi || '-')}</span></div><div><b>本页 OCR 字数</b><span>${p.chars || 0}</span></div></div><details open><summary>输入阶段 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify(data, null, 2))}</pre></details></div>`;
}
async function loadOcrStage() {
  textBox.innerHTML = ocrSummary(pageMeta(currentPage)) + '<pre></pre>';
  const r = await fetch(`/job/${currentJob}/text/${currentPage}`), d = await r.json();
  textBox.querySelector('pre').textContent = d.text || d.error || '';
}
async function getExtracted() {
  const r = await fetch(`/api/jobs/${currentJob}/pages/${currentPage}/extracted`);
  return await r.json();
}
function structuredRows(fields, editable) {
  return Object.entries(fields || {}).map(([k, f]) => {
    const value = editable ? `<input data-field="${escapeHtml(k)}" value="${escapeHtml(f.value ?? '')}"><div class="field-note">${escapeHtml(f.review_reason || '')}</div>` : renderJsonValue(f.value);
    const ev = f.evidence || {};
    const evidence = ev.ocr_text ? `${ev.ocr_text} · OCR ${confidenceText(ev.ocr_score)}` : '无匹配 OCR 证据';
    return `<tr><td>${escapeHtml(fieldLabel(k))}</td><td>${value}</td><td><span class="chip">${escapeHtml(f.source || 'unknown')}</span></td><td>${confidenceText(f.confidence)}</td><td>${fieldStatusChip(f)}</td><td class="field-evidence">${escapeHtml(evidence)}</td></tr>`;
  }).join('') || '<tr><td colspan="6">暂无字段</td></tr>';
}
async function loadExtractStage() {
  const d = await getExtracted();
  if (d.status === 'none') {
    textBox.innerHTML = `<div class="structured-empty"><pre>${escapeHtml(d.message || '尚未结构化提取。AI 报告完成后会自动生成。')}</pre><button id="runExtractNow" class="secondary" type="button">立即结构化</button></div>`;
    $('runExtractNow').onclick = runExtractNow;
    return;
  }
  textBox.innerHTML = `<div class="structured"><div class="structured-head"><div><b>04 结构化提取 · ${escapeHtml(d.doc_type || '未知文档')}</b><span>按 JSON Schema 归并字段，JSON 展示以这里的结构化结果为准。</span></div><span class="chip ok">Schema 输出</span></div><table class="field-table"><thead><tr><th>字段</th><th>值</th><th>来源</th><th>置信度</th><th>状态</th><th>证据</th></tr></thead><tbody>${structuredRows(d.fields, false)}</tbody></table><details><summary>结构化 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify(d, null, 2))}</pre></details></div>`;
}
async function loadRulesStage() {
  const d = await getExtracted();
  if (d.status === 'none') {
    textBox.innerHTML = `<div class="structured-empty"><pre>${escapeHtml(d.message || '等待结构化结果后执行规则校验。')}</pre></div>`;
    return;
  }
  const v = d.validation || {};
  const errs = (v.errors || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const warns = (v.warnings || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const lows = (v.low_confidence_fields || []).map(x => `<li>${escapeHtml(fieldLabel(x))}</li>`).join('');
  const rules = (v.rules || []).map(x => `<tr><td>${escapeHtml(x.code || '-')}</td><td>${escapeHtml(x.severity || '-')}</td><td>${escapeHtml(x.message || '-')}</td></tr>`).join('') || '<tr><td colspan="3">未命中硬规则</td></tr>';
  textBox.innerHTML = `<div class="stage-panel"><div class="stage-head"><div><b>05 规则校验</b><span>对结构化字段做金额、日期、重复、必填和低置信度等硬规则判断。</span></div><span class="chip ${errs ? 'bad' : warns || lows ? 'warn' : 'ok'}">${errs ? '有错误' : warns || lows ? '有提醒' : '通过'}</span></div>${errs ? `<div class="review-alert compact"><b>错误</b><ul>${errs}</ul></div>` : ''}${warns ? `<div class="review-alert compact"><b>警告</b><ul>${warns}</ul></div>` : ''}${lows ? `<div class="rules-box"><b>低置信字段</b><ul>${lows}</ul></div>` : ''}<table class="field-table"><thead><tr><th>规则</th><th>级别</th><th>说明</th></tr></thead><tbody>${rules}</tbody></table><details><summary>规则校验 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify(v, null, 2))}</pre></details></div>`;
}
async function loadRoutingStage() {
  const d = await getExtracted();
  if (d.status === 'none') {
    textBox.innerHTML = `<div class="structured-empty"><pre>${escapeHtml(d.message || '等待规则校验后进行置信度路由。')}</pre></div>`;
    return;
  }
  const v = d.validation || {};
  const reasons = [
    ...(v.errors || []).map(x => `错误：${x}`),
    ...(v.warnings || []).map(x => `警告：${x}`),
    ...(v.low_confidence_fields || []).map(x => `低置信字段：${fieldLabel(x)}`)
  ];
  const cards = [
    ['最终路由', routingText(d.routing)],
    ['提取置信度', confidenceText(d.extraction_confidence)],
    ['低置信字段', (v.low_confidence_fields || []).map(fieldLabel).join('、') || '无'],
    ['人工状态', d.manual_status || '未处理']
  ].map(([k, val]) => `<div><b>${escapeHtml(k)}</b><span>${escapeHtml(val)}</span></div>`).join('');
  textBox.innerHTML = `<div class="stage-panel"><div class="stage-head"><div><b>06 置信度路由</b><span>规则结果和字段置信度共同决定自动通过、进入人工审核或拒绝。</span></div>${routingChip(d) || '<span class="chip">等待</span>'}</div><div class="stage-grid">${cards}</div><div class="rules-box"><b>路由原因</b><ul>${reasons.map(x => `<li>${escapeHtml(x)}</li>`).join('') || '<li>未发现需要拦截的原因</li>'}</ul></div><details open><summary>路由 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify({routing: d.routing, confidence: d.extraction_confidence, validation: v, manual_status: d.manual_status || null}, null, 2))}</pre></details></div>`;
}
async function loadStructured() {
  const d = await getExtracted();
  if (d.status === 'none') {
    textBox.innerHTML = `<div class="structured-empty"><pre>${escapeHtml(d.message || '尚未结构化提取。AI 报告完成后会自动生成。')}</pre><button id="runExtractNow" class="secondary" type="button">立即结构化</button></div>`;
    $('runExtractNow').onclick = runExtractNow;
    return;
  }
  const validation = d.validation || {}, rules = validation.rules || [];
  const errs = (validation.errors || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const warns = (validation.warnings || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
  const ruleRows = rules.map(x => `<li><b>${escapeHtml(x.code)}</b> · ${escapeHtml(x.severity)} · ${escapeHtml(x.message)}</li>`).join('');
  textBox.innerHTML = `<div class="structured"><div class="structured-head"><div><b>07 人工复核 · Page ${currentPage} · ${escapeHtml(d.doc_type || '未知文档')}</b><span>${routingText(d.routing)} · 置信度 ${Math.round((d.extraction_confidence || 0) * 100)}%</span></div><div class="tabgroup"><button id="saveFields" class="secondary" type="button">保存字段</button><button id="approvePage" type="button">通过</button><button id="rejectPage" class="danger" type="button">拒绝</button></div></div><label>人工处理原因</label><input id="reviewReason" class="review-reason" value="${escapeHtml(d.manual_reason || '')}">${errs ? `<div class="review-alert compact"><b>错误</b><ul>${errs}</ul></div>` : ''}${warns ? `<div class="review-alert compact"><b>警告</b><ul>${warns}</ul></div>` : ''}${ruleRows ? `<div class="rules-box"><b>规则命中</b><ul>${ruleRows}</ul></div>` : ''}<table class="field-table"><thead><tr><th>字段</th><th>值</th><th>来源</th><th>置信度</th><th>状态</th><th>证据</th></tr></thead><tbody>${structuredRows(d.fields, true)}</tbody></table></div>`;
  $('saveFields').onclick = saveStructuredFields;
  $('approvePage').onclick = () => manualReview('approve');
  $('rejectPage').onclick = () => manualReview('reject');
}
async function runExtractNow() {
  if (!currentJob || !currentPage) return;
  textBox.innerHTML = '<pre>正在结构化提取...</pre>';
  const r = await fetch(`/api/jobs/${currentJob}/pages/${currentPage}/extract`, {method: 'POST'});
  if (!r.ok) {
    const d = await r.json();
    textBox.innerHTML = `<pre>${escapeHtml(d.error || '结构化提取失败')}</pre>`;
    return;
  }
  await poll();
  currentStage = 'extract';
  await loadCurrent();
}
async function saveStructuredFields() {
  const fields = {}, reason = $('reviewReason')?.value || '';
  for (const input of textBox.querySelectorAll('[data-field]')) fields[input.dataset.field] = {value: input.value};
  const r = await fetch(`/api/jobs/${currentJob}/pages/${currentPage}/fields`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({fields, reason})});
  if (!r.ok) {
    const d = await r.json();
    showMessage(d.error || '保存失败');
    return;
  }
  await poll();
  await loadStructured();
  showMessage(`第 ${currentPage} 页字段已保存`);
}
async function manualReview(action) {
  const reason = $('reviewReason')?.value || '';
  const r = await fetch(`/api/jobs/${currentJob}/pages/${currentPage}/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({reason})});
  if (!r.ok) {
    const d = await r.json();
    showMessage(d.error || '操作失败');
    return;
  }
  await poll();
  await loadStructured();
  showMessage(`第 ${currentPage} 页已${action === 'approve' ? '通过' : '拒绝'}`);
}

async function rerunCurrentOcr() {
  if (!currentJob || !currentPage) return;
  const btn = $('rerunOcr'), old = btn.textContent, fd = new FormData(form);
  btn.disabled = true;
  btn.textContent = '重跑中...';
  showMessage(`正在重跑第 ${currentPage} 页 OCR`);
  try {
    const r = await fetch(`/api/jobs/${currentJob}/ocr/${currentPage}/rerun`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dpi: fd.get('dpi'), keywords: fd.get('keywords'), ocr_reorder: fd.get('ocr_reorder') === 'on', low_conf_threshold: fd.get('low_conf_threshold')})
    });
    const d = await r.json();
    if (!r.ok) {showMessage(d.error || '重跑 OCR 失败'); return;}
    currentStage = 'ocr';
    await poll();
    await loadCurrent();
    showMessage(`第 ${currentPage} 页 OCR 已重跑`);
  } finally {
    btn.textContent = old;
    btn.disabled = !currentPage;
  }
}
async function startVlm() {
  if (!currentJob) return;
  const list = [...selectedPages].sort((a, b) => a - b);
  if (!list.length && currentPage) list.push(currentPage);
  if (!list.length) {showMessage('请选择页面'); return;}
  const apiKey = new FormData(form).get('api_key') || '';
  const saveApiKey = new FormData(form).get('save_api_key') === 'on';
  if (!apiKey && !(lastJob && lastJob.vlm_configured)) {showMessage('大模型未配置，仅支持文本识别'); return;}
  const runBtn = $('runVlm'), oldText = runBtn.textContent;
  currentStage = 'vlm';
  renderPipeline();
  runBtn.disabled = true;
  runBtn.textContent = '提交中...';
  showMessage(`正在提交 AI 提取任务，共 ${list.length} 页`);
  textBox.innerHTML = `<pre>正在提交 AI 提取任务，共 ${list.length} 页...</pre>`;
  try {
    const r = await fetch(`/api/jobs/${currentJob}/vlm`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pages: list, api_key: apiKey, save_api_key: saveApiKey})});
    const d = await r.json();
    if (!r.ok) {
      showMessage(d.error || 'AI 提取提交失败');
      textBox.innerHTML = `<pre>${escapeHtml(d.error || 'AI 提取提交失败')}</pre>`;
      return;
    }
    const rows = d.results || [], queued = rows.filter(x => x.status === 'queued').length, running = rows.filter(x => x.status === 'running').length, done = rows.filter(x => x.status === 'done').length;
    const msg = `AI 提取已提交：${list.length} 页${queued ? `，新加入 ${queued} 页` : ''}${running ? `，处理中 ${running} 页` : ''}${done ? `，已完成 ${done} 页` : ''}`;
    showMessage(msg);
    textBox.innerHTML = `<pre>${escapeHtml(msg)}</pre>`;
    if (!timer) timer = setInterval(poll, 1200);
    await poll();
    await loadLocalSettings();
    showMessage(msg);
  } finally {
    runBtn.textContent = oldText;
    runBtn.disabled = !currentPage;
  }
}

function renderMarkdown(src) {
  let s = escapeHtml(src || ''), blocks = [];
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `@@CODE${blocks.push('<pre><code>' + code + '</code></pre>') - 1}@@`);
  let out = '', inList = false, inTable = false;
  const closeList = () => {if (inList) {out += '</ul>'; inList = false;}};
  const closeTable = () => {if (inTable) {out += '</tbody></table>'; inTable = false;}};
  for (const line of s.split('\n')) {
    const trimmed = line.trim();
    const isTable = /^\|.*\|$/.test(trimmed);
    const isSep = /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(trimmed);
    if (isTable && !isSep) {
      closeList();
      const cells = trimmed.replace(/^\||\|$/g, '').split('|').map(x => x.trim());
      if (!inTable) {out += '<table><thead><tr>' + cells.map(c => `<th>${c}</th>`).join('') + '</tr></thead><tbody>'; inTable = true;}
      else out += '<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>';
      continue;
    }
    if (isSep) continue;
    closeTable();
    if (/^---+$/.test(trimmed)) {closeList(); out += '<hr>'; continue;}
    if (/^###\s+/.test(line)) {closeList(); out += '<h3>' + line.replace(/^###\s+/, '') + '</h3>'; continue;}
    if (/^##\s+/.test(line)) {closeList(); out += '<h2>' + line.replace(/^##\s+/, '') + '</h2>'; continue;}
    if (/^#\s+/.test(line)) {closeList(); out += '<h1>' + line.replace(/^#\s+/, '') + '</h1>'; continue;}
    if (/^\s*[-*]\s+/.test(line)) {if (!inList) {out += '<ul>'; inList = true;} out += '<li>' + line.replace(/^\s*[-*]\s+/, '') + '</li>'; continue;}
    if (!trimmed) {closeList(); continue;}
    closeList();
    out += '<p>' + line + '</p>';
  }
  closeList();
  closeTable();
  return out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/@@CODE(\d+)@@/g, (_, i) => blocks[Number(i)]);
}
function needsReviewFallback(text) {
  const s = String(text || '').replace(/\s+/g, '');
  if (/复核状态[:：]?需要人工复核/.test(s)) return true;
  if (/复核状态[:：]?通过/.test(s)) return false;
  return /(需要人工复核|OCR不一致|识别不一致|存在差异|无法确认|不确定|看不清|多个候选|冲突)/.test(s);
}
function vlmSubtabs() {
  return `<div class="subtabs"><button id="vlmPretty" class="${vlmView === 'pretty' ? 'active' : ''}" type="button">美化展示</button><button id="vlmJson" class="${vlmView === 'json' ? 'active' : ''}" type="button">JSON 展示</button></div>`;
}
function bindVlmSubtabs() {
  const pretty = $('vlmPretty'), json = $('vlmJson');
  if (pretty) pretty.onclick = () => {vlmView = 'pretty'; loadVlm();};
  if (json) json.onclick = () => {vlmView = 'json'; loadVlm();};
}
function renderJsonValue(value) {
  if (value === null || value === undefined) return '<span class="json-null">null</span>';
  if (typeof value === 'boolean') return `<span class="json-bool">${value}</span>`;
  if (typeof value === 'number') return `<span class="json-num">${value}</span>`;
  if (typeof value === 'string') return `<span class="json-str">${escapeHtml(value)}</span>`;
  return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
}
function renderStructuredJson(extracted, aiReport) {
  if (!extracted || extracted.status === 'none') {
    return `<div class="json-panel"><div class="empty">尚未生成结构化结果。AI 原始报告只适合调试，不建议作为业务入库结果。</div><details><summary>原始 AI 报告 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify(aiReport, null, 2))}</pre></details></div>`;
  }
  const fields = Object.entries(extracted.fields || {}).map(([k, f]) => `<tr><td>${escapeHtml(fieldLabel(k))}</td><td>${renderJsonValue(f.value)}</td><td>${escapeHtml(f.source || 'unknown')}</td><td>${confidenceText(f.confidence)}</td><td>${fieldStatusChip(f)}</td><td>${escapeHtml(f.review_reason || '')}</td></tr>`).join('') || '<tr><td colspan="6">暂无字段</td></tr>';
  const validation = extracted.validation || {};
  const rules = (validation.rules || []).map(x => `<li><b>${escapeHtml(x.code)}</b> · ${escapeHtml(x.severity)} · ${escapeHtml(x.message)}</li>`).join('') || '<li>无</li>';
  return `<div class="json-panel"><div class="structured-head"><div><b>结构化业务结果</b><span>${escapeHtml(extracted.doc_type || '未知文档')} · ${routingText(extracted.routing)} · 置信度 ${confidenceText(extracted.extraction_confidence)}</span></div></div><table class="field-table"><thead><tr><th>字段</th><th>值</th><th>来源</th><th>置信度</th><th>状态</th><th>原因</th></tr></thead><tbody>${fields}</tbody></table><div class="rules-box"><b>规则/校验</b><ul>${rules}</ul></div><details><summary>结构化结果原始 JSON</summary><pre class="json-view">${escapeHtml(JSON.stringify(extracted, null, 2))}</pre></details><details><summary>原始 AI 报告 JSON（调试用）</summary><pre class="json-view">${escapeHtml(JSON.stringify(aiReport, null, 2))}</pre></details></div>`;
}
async function loadVlm() {
  const r = await fetch(`/api/jobs/${currentJob}/vlm/${currentPage}`), d = await r.json();
  let extracted = null;
  try {extracted = await getExtracted();} catch (e) {extracted = null;}
  $('vlmStatus').textContent = d.status === 'none' ? (lastJob?.vlm_configured ? '未提取' : '大模型未配置，仅支持文本识别') : `${d.status} ${d.seconds ? d.seconds + 's' : ''}`;
  if (d.status === 'done' && d.text) {
    if (vlmView === 'json') {
      textBox.innerHTML = vlmSubtabs() + renderStructuredJson(extracted, d);
      bindVlmSubtabs();
      return;
    }
    const stale = d.stale_after_ocr ? '<div class="review-alert compact"><b>AI 结果可能已过期</b><span>本页 OCR 已重跑，建议重新执行 AI 提取。</span></div>' : '';
    const reviewRequired = Boolean(d.review_required) || needsReviewFallback(d.text);
    const reasons = (d.review_reasons || []).map(x => `<li>${escapeHtml(x)}</li>`).join('');
    const review = reviewRequired ? `<div class="review-alert"><b>需要人工复核</b><div>OCR 文本与 AI 核对结果存在差异或不确定项。</div>${reasons ? `<ul>${reasons}</ul>` : ''}</div>` : `<div class="review-ok"><b>复核通过</b><span>AI 未标记明显差异。</span></div>`;
    textBox.innerHTML = `${vlmSubtabs()}${stale}${review}<div class="md">${renderMarkdown(d.text)}</div>`;
    bindVlmSubtabs();
  } else {
    textBox.innerHTML = `${vlmSubtabs()}<pre>${escapeHtml(d.message || '尚未提取。需要时点击“AI 提取选中页”。')}</pre>`;
    bindVlmSubtabs();
  }
}

loadHistory();
loadKeywordHistory();
loadLocalSettings();
renderPages();
renderPipeline();
