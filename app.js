// keyword-tracker frontend app

let pendingData = null;
let allData = {};
let activeTab = null;
let authToken = localStorage.getItem('kw_auth') || '';
// Keyword order per ASIN: { asin: { natural: [kw1, kw2, ...], ad: [kw1, kw2, ...] } }
let kwOrder = JSON.parse(localStorage.getItem('kw_order') || '{}');

function saveKwOrder() { localStorage.setItem('kw_order', JSON.stringify(kwOrder)); }

// ========== Auth ==========
// 所有 /api 请求都要带登录令牌（X-Auth），令牌失效时退回登录页。
function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (authToken) h['X-Auth'] = authToken;
  return h;
}
function logout() {
  authToken = '';
  localStorage.removeItem('kw_auth');
  document.getElementById('mainApp').style.display = 'none';
  document.getElementById('loginOverlay').style.display = 'flex';
  document.getElementById('passwordInput').value = '';
}
async function apiFetch(url, options) {
  const opts = Object.assign({}, options || {});
  opts.headers = authHeaders(opts.headers);
  const resp = await fetch(url, opts);
  if (resp.status === 401) { logout(); throw new Error('登录已过期，请重新登录'); }
  return resp;
}
async function checkAuth() {
  if (authToken) {
    try { const resp = await fetch('/api/data', { headers: authHeaders() }); if (resp.ok) { showMain(); return; } } catch(e) {}
    authToken = '';
    localStorage.removeItem('kw_auth');
  }
  document.getElementById('loginOverlay').style.display = 'flex';
  document.getElementById('mainApp').style.display = 'none';
}
async function verifyPassword() {
  const pwd = document.getElementById('passwordInput').value.trim();
  if (!pwd) return;
  try {
    const resp = await fetch('/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pwd }) });
    const data = await resp.json();
    if (data.ok) { authToken = data.token; if (document.getElementById('rememberPwd').checked) localStorage.setItem('kw_auth', authToken); showMain(); document.getElementById('loginError').style.display = 'none'; }
    else document.getElementById('loginError').style.display = 'block';
  } catch(e) { document.getElementById('loginError').style.display = 'block'; }
}
function showMain() { document.getElementById('loginOverlay').style.display = 'none'; document.getElementById('mainApp').style.display = 'block'; refreshData(); }

// ========== Data Loading ==========
async function refreshData() {
  try {
    const resp = await apiFetch('/api/data');
    if (!resp.ok) { logout(); return; }
    allData = await resp.json();
    renderTabs(); renderPreview();
    document.getElementById('statusBar').textContent = `共 ${Object.keys(allData).length} 个产品，最后更新: ${new Date().toLocaleString()}`;
  } catch (err) { document.getElementById('statusBar').textContent = '加载失败: ' + err.message; }
}

// ========== Import (always force overwrite) ==========
async function importData() {
  const input = document.getElementById('jsonInput').value.trim();
  if (!input) return alert('请粘贴 JSON 数据');
  let data;
  try { data = JSON.parse(input); } catch (e) {
    document.getElementById('importStatus').innerHTML = '<div class="alert alert-error">JSON 解析失败: ' + e.message + '</div>'; return;
  }
  const btn = document.querySelector('.btn-primary');
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 提交中...';
  document.getElementById('importStatus').innerHTML = '';
  try {
    const resp = await apiFetch('/api/import?force=true', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const result = await resp.json();
    if (result.status === 'success') {
      document.getElementById('importStatus').innerHTML = '<div class="alert alert-success">✅ 导入成功！' + result.products + ' 个产品，' + result.rankings + ' 条排名记录</div>';
      document.getElementById('jsonInput').value = ''; refreshData();
    } else throw new Error(result.error || 'Unknown error');
  } catch (err) { document.getElementById('importStatus').innerHTML = '<div class="alert alert-error">❌ 导入失败: ' + err.message + '</div>'; }
  btn.disabled = false; btn.innerHTML = '📤 提交数据';
}

// ========== Export ==========
function exportUrl(asin) { return asin ? '/api/export?asin=' + encodeURIComponent(asin) + '&order=' + encodeURIComponent(JSON.stringify(kwOrder[asin] || {})) : '/api/export?order=' + encodeURIComponent(JSON.stringify(kwOrder)); }
async function downloadExcel(asin) {
  try {
    const r = await apiFetch(exportUrl(asin));
    if (!r.ok) throw new Error('导出失败');
    const b = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = '\u5173\u952E\u8BCD\u8BB0\u5F55.xls';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch(e) { alert('导出失败: ' + e.message); }
}
function exportExcel() { return downloadExcel(''); }
function exportSingle(asin) { return downloadExcel(asin); }

// ========== Multi-Delete ==========
function showDeleteModal(asin) {
  const product = allData[asin];
  if (!product) return;
  const dbDates = [...new Set(Object.keys(product.dates))].sort();
  const minD = dbDates[0] || '2025-11-01', maxD = dbDates[dbDates.length - 1] || '2026-06-30';
  
  document.getElementById('deleteMsg').innerHTML = `产品: <b>${product.name}</b> (${asin})<br><span style="font-size:12px;color:#666;">数据范围: ${minD} ~ ${maxD}，共 ${dbDates.length} 个有数据日期</span>`;
  
  let html = '<div class="row"><label>起始日期:</label><input type="date" id="delStart" value="' + minD + '" style="flex:1;"><label>结束日期:</label><input type="date" id="delEnd" value="' + maxD + '" style="flex:1;"></div>';
  html += '<p id="deleteInfo" style="margin-top:8px;font-size:12px;color:#666;">将删除从起始到结束（含）的所有数据</p>';
  
  document.getElementById('deleteDateContainer').innerHTML = html;
  document.getElementById('deleteConfirmBtn').onclick = confirmDelete;
  document.getElementById('deleteConfirmBtn').disabled = false;
  document.getElementById('deleteModal').classList.add('show');
  document.getElementById('deleteModal').dataset.asin = asin;
}

function closeDeleteModal() { document.getElementById('deleteModal').classList.remove('show'); }

async function confirmDelete() {
  const asin = document.getElementById('deleteModal').dataset.asin;
  const start = document.getElementById('delStart').value;
  const end = document.getElementById('delEnd').value;
  if (!asin || !start || !end) return;
  
  // Generate all dates in range
  const dates = [];
  let cur = new Date(start);
  const endD = new Date(end);
  while (cur <= endD) {
    dates.push(cur.toISOString().substring(0, 10));
    cur.setDate(cur.getDate() + 1);
  }
  
  if (!confirm(`确定删除 ${asin} 从 ${start} 到 ${end} 共 ${dates.length} 天的数据？`)) return;
  
  let success = 0;
  for (const ds of dates) {
    try { const r = await apiFetch(`/api/data?asin=${encodeURIComponent(asin)}&date=${encodeURIComponent(ds)}`, { method: 'DELETE' }); if (r.ok) success++; } catch(e) {}
  }
  alert(`✅ 已删除 ${success}/${dates.length} 天数据`);
  closeDeleteModal();
  refreshData();
}

// ========== Drag to reorder ==========
let dragSrcRow = null, dragSection = '';

function getKwOrder(asin) {
  if (!kwOrder[asin]) kwOrder[asin] = { natural: [], ad: [] };
  return kwOrder[asin];
}

function applyOrder(asin, kwList) {
  const order = getKwOrder(asin);
  // Build ordered list: first from saved order, then append new unknown keywords
  const resultNatural = [];
  const resultAd = [];
  const remainingN = new Set(kwList), remainingA = new Set(kwList);
  
  for (const kw of order.natural) { if (remainingN.has(kw)) { resultNatural.push(kw); remainingN.delete(kw); } }
  for (const kw of remainingN) resultNatural.push(kw);
  for (const kw of order.ad) { if (remainingA.has(kw)) { resultAd.push(kw); remainingA.delete(kw); } }
  for (const kw of remainingA) resultAd.push(kw);
  
  order.natural = resultNatural;
  order.ad = resultAd;
  saveKwOrder();
  return { natural: resultNatural, ad: resultAd };
}

function renderPreview() {
  const container = document.getElementById('previewTable');
  const asin = activeTab;
  if (!asin || !allData[asin]) { container.innerHTML = '<p style="color:#999;padding:20px;">请先导入数据</p>'; return; }
  
  const product = allData[asin];
  const dates = Object.keys(product.dates).sort();
  const kwSet = new Set();
  for (const dd of Object.values(product.dates)) for (const kw of Object.keys(dd.keywords)) kwSet.add(kw);
  const orderedKws = applyOrder(asin, [...kwSet]);
  
  if (dates.length === 0) { container.innerHTML = '<p style="color:#999;padding:20px;">该产品暂无数据</p>'; return; }
  
  let html = '<table class="preview"><thead>';
  html += '<tr class="row-normal"><td class="td-date" style="font-weight:bold;">' + product.name + '<br><small>' + asin + '</small></td>';
  for (const d of dates) html += '<td class="td-date">' + formatDateChinese(d) + '</td>';
  html += '</tr>';
  html += '<tr class="row-rank"><td class="td-rank" style="font-weight:bold;">Rank</td>';
  for (const d of dates) { const dd = product.dates[d]; html += '<td class="td-rank">' + (dd ? (dd.rank || '').replace(/\n/g, '<br>') : '') + '</td>'; }
  html += '</tr>';
  html += '<tr class="row-normal"><td class="td-center">评分 / 评论</td>';
  for (const d of dates) { const dd = product.dates[d]; html += '<td class="td-center">' + (dd ? dd.rating + ' / ' + dd.reviewCount : '') + '</td>'; }
  html += '</tr>';
  
  // Natural section
  html += '<tr class="row-normal"><td class="td-section" colspan="' + (dates.length + 1) + '" style="background:#5B9BD5;color:#fff;">自然位-精准词</td></tr>';
  for (const kw of orderedKws.natural) {
    html += '<tr class="row-normal kw-drag" draggable="true" data-section="natural" data-kw="' + kw.replace(/"/g,'&quot;') + '" ondragstart="dragStart(event)" ondragover="dragOver(event)" ondrop="dropKw(event)">';
    html += '<td style="text-align:left;cursor:grab;">⋮⋮ ' + kw + ' <span style="color:#d93025;cursor:pointer;font-size:14px;" onclick="event.stopPropagation();deleteKeyword(\'' + asin + '\',\'' + kw.replace(/'/g,'\\\'') + '\')" title="删除此关键词">✕</span></td>';
    for (const d of dates) { const dd = product.dates[d]; html += '<td class="td-center">' + ((dd && dd.keywords[kw]) ? dd.keywords[kw].naturalPos : '') + '</td>'; }
    html += '</tr>';
  }
  html += '<tr class="row-normal"><td colspan="' + (dates.length + 1) + '" style="background:#f0f0f0;"></td></tr>';
  
  // Ad section
  html += '<tr class="row-normal"><td class="td-section" colspan="' + (dates.length + 1) + '" style="background:#5B9BD5;color:#fff;">广告位-精准词</td></tr>';
  for (const kw of orderedKws.ad) {
    html += '<tr class="row-normal kw-drag" draggable="true" data-section="ad" data-kw="' + kw.replace(/"/g,'&quot;') + '" ondragstart="dragStart(event)" ondragover="dragOver(event)" ondrop="dropKw(event)">';
    html += '<td style="text-align:left;cursor:grab;">⋮⋮ ' + kw + ' <span style="color:#d93025;cursor:pointer;font-size:14px;" onclick="event.stopPropagation();deleteKeyword(\'' + asin + '\',\'' + kw.replace(/'/g,'\\\'') + '\')" title="删除此关键词">✕</span></td>';
    for (const d of dates) { const dd = product.dates[d]; html += '<td class="td-center">' + ((dd && dd.keywords[kw]) ? dd.keywords[kw].adPos : '') + '</td>'; }
    html += '</tr>';
  }
  html += '</table>';
  
  html += '<div class="row" style="margin-top:8px;">';
  html += '<button class="btn btn-danger btn-sm" onclick="showDeleteModal(\'' + asin + '\')">🗑 删除数据</button>';
  html += '<span style="flex:1;"></span>';
  html += '<button class="btn btn-outline btn-sm" onclick="exportSingle(\'' + asin + '\')">📥 导出此产品</button>';
  html += '</div>';
  container.innerHTML = html;
}

function dragStart(e) {
  dragSrcRow = e.target.closest('.kw-drag');
  if (!dragSrcRow) return;
  dragSection = dragSrcRow.dataset.section;
  e.dataTransfer.effectAllowed = 'move';
}

function dragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }

function dropKw(e) {
  e.preventDefault();
  const target = e.target.closest('.kw-drag');
  if (!target || !dragSrcRow || target === dragSrcRow) return;
  if (target.dataset.section !== dragSection) return; // Only same section
  
  const asin = activeTab;
  const order = getKwOrder(asin);
  const list = dragSection === 'natural' ? order.natural : order.ad;
  const srcKw = dragSrcRow.dataset.kw, tgtKw = target.dataset.kw;
  const srcIdx = list.indexOf(srcKw), tgtIdx = list.indexOf(tgtKw);
  if (srcIdx >= 0 && tgtIdx >= 0) {
    list.splice(srcIdx, 1);
    list.splice(tgtIdx, 0, srcKw);
    saveKwOrder();
    renderPreview();
  }
}

async function deleteKeyword(asin, keyword) {
  if (!confirm('确定删除产品 ' + asin + ' 的关键词 "' + keyword + '" 的所有数据？')) return;
  try {
    const resp = await apiFetch('/api/data?asin=' + encodeURIComponent(asin) + '&keyword=' + encodeURIComponent(keyword), { method: 'DELETE' });
    const result = await resp.json();
    if (result.status === 'deleted') { alert('✅ 已删除 ' + result.changes + ' 条记录'); refreshData(); }
  } catch(e) { alert('删除失败: ' + e.message); }
}

async function renameProduct(asin) {
  const newName = prompt('请输入新名称:', (allData[asin] && allData[asin].name) || '');
  if (!newName) return;
  try {
    const resp = await apiFetch('/api/data', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ asin, name: newName }) });
    const result = await resp.json();
    if (result.status === 'updated') { refreshData(); }
  } catch(e) { alert('修改失败: ' + e.message); }
}

function renderTabs() {
  const tabs = document.getElementById('productTabs');
  const asins = Object.keys(allData).sort();
  if (asins.length === 0) { tabs.innerHTML = '<span style="color:#999;padding:8px;">暂无数据</span>'; return; }
  tabs.innerHTML = asins.map(a => {
    const n = allData[a].name || a;
    return `<div class="tab${a===activeTab?' active':''}">
<span onclick="selectTab('${a}')">${n}<br><small>${a}</small></span>
<span style="font-size:10px;color:#666;cursor:pointer;margin-left:4px;" onclick="event.stopPropagation();renameProduct('${a}')" title="修改名称">✎</span>
</div>`;
  }).join('');
  if (!activeTab || !asins.includes(activeTab)) { activeTab = asins[0]; tabs.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',asins[i]===activeTab)); }
}

function selectTab(asin) { activeTab = asin; renderTabs(); renderPreview(); }

function formatDateChinese(iso) {
  const p = iso.split('-');
  if (p.length !== 3) return iso;
  return parseInt(p[1]) + '月' + parseInt(p[2]) + '日';
}

checkAuth();
