// ============ Global ============
var C = {}, currentTab = 'overview', execHistoryData = [], execHistoryFilter = 'all';
var expiredDirEntries = [], autoSaveTimer = null, logPollTimer = null;
var logBefore = [], logFilter = 'all', logPaused = false;
var logPollInterval = 0;
var sseConnected = false;
var LOG_BEFORE_MAX = 500;
var APP_PATHS = {category_base:'/影视', search:'/批量转存/手动搜索存', tmdb:'/批量转存/TMDB'};
var SETTINGS_ALL = null;  // 缓存 /api/settings/all，调度页与设置页共用，避免重复请求

// TMDB 海报基础地址（OPT-48）：与设置 cfg_tmdb_base_url 呼应，避免硬编码。
var TMDB_IMG_BASE = 'https://image.tmdb.org/t/p/w185';

// 统一「下次调度时间」格式化（OPT-35）："2024-01-01 12:00:00" → "01-01 12:00"，空值回退"—"。
function fmtNextTime(s){
  return s ? String(s).slice(5, 16) : '—';
}


// P1 UX: password visibility toggle
function togglePwd(btn){
  var input = btn.previousElementSibling;
  if(!input||!input.tagName||input.tagName.toLowerCase()!=='input')return;
  var isPwd = input.type==='password';
  input.type = isPwd ? 'text' : 'password';
  btn.querySelector('use').setAttribute('href', isPwd ? '#icon-eye-off' : '#icon-eye');
  btn.setAttribute('aria-label', isPwd ? '隐藏密码' : '显示密码');
  btn.setAttribute('title', isPwd ? '隐藏密码' : '显示密码');
}


// P3 a11y: announce milestone status to screen readers via a polite live region
function srAnnounce(msg){
  var el = document.getElementById('srLive');
  if(!el || !msg) return;
  el.textContent = '';
  setTimeout(function(){ el.textContent = msg; }, 40);
}

// 外观：密度切换（宽松 / 标准 / 紧凑），纯客户端偏好；
// 主要影响 TMDB 资源网格列数（每屏可见资源数量），紧凑态同时收紧全局间距
var DENSITY_ORDER = ['comfortable','standard','compact'];
var DENSITY_LABEL = {comfortable:'宽松', standard:'标准', compact:'紧凑'};

function initDensity(){
  var d = localStorage.getItem('density');
  if(DENSITY_ORDER.indexOf(d) === -1) d = 'standard';
  document.documentElement.setAttribute('data-density', d);
  updateDensityBtn();
}
function toggleDensity(){
  var cur = document.documentElement.getAttribute('data-density') || 'standard';
  var idx = DENSITY_ORDER.indexOf(cur);
  if(idx === -1) idx = 1;
  var next = DENSITY_ORDER[(idx + 1) % DENSITY_ORDER.length];
  document.documentElement.setAttribute('data-density', next);
  localStorage.setItem('density', next);
  updateDensityBtn();
  srAnnounce('TMDB 显示密度已切换为' + (DENSITY_LABEL[next] || next));
}
function updateDensityBtn(){
  var btn = document.getElementById('densityBtn');
  if(!btn) return;
  var cur = document.documentElement.getAttribute('data-density') || 'standard';
  var label = DENSITY_LABEL[cur] || cur;
  var tip = 'TMDB 显示密度：' + label + '（点击切换）';
  btn.title = tip;
  btn.setAttribute('aria-label', tip);
  btn.dataset.density = cur;
}

// ============ 命令面板（⌘K / Ctrl+K） ============
// 全局快捷操作入口：切 tab、触发转存、切换主题/密度等。
var CMD_LIST = [
  {title:'前往 概览', icon:'icon-dashboard', hint:'首页', run:function(){ switchTab('overview'); }},
  {title:'前往 发现（TMDB）', icon:'icon-tmdb', hint:'浏览 / 转存影视', run:function(){ switchTab('tmdb'); }},
  {title:'前往 转存', icon:'icon-send', hint:'手动转存', run:function(){ switchTab('manual'); }},
  {title:'前往 定时', icon:'icon-clock', hint:'定时任务', run:function(){ switchTab('schedule'); }},
  {title:'前往 历史', icon:'icon-check-circle', hint:'执行历史', run:function(){ switchTab('history'); }},
  {title:'前往 设置', icon:'icon-settings', hint:'系统配置', run:function(){ switchTab('settings'); }},
  {title:'开始转存（转存页）', icon:'icon-transfer', hint:'Ctrl/⌘ + Enter', run:function(){ if(currentTab==='manual'){ var s=document.getElementById('stopBtn'); if(!(s&&s.style.display!=='none')) startTransfer(); } }},
  {title:'刷新 TMDB 缓存', icon:'icon-refresh', hint:'', run:function(){ refreshTmdbCache(); }},
  {title:'切换主题（深 / 浅 / 跟随系统）', icon:'icon-contrast', hint:'', run:function(){ toggleTheme(); }},
  {title:'切换 TMDB 显示密度', icon:'icon-filter', hint:'', run:function(){ toggleDensity(); }},
  {title:'清空 TMDB 选择', icon:'icon-delete', hint:'', run:function(){ clearTmdbSelection(); }},
  {title:'聚焦资源搜索', icon:'icon-search', hint:'', run:function(){ var sb=document.getElementById('searchInput'); if(sb){ sb.focus(); sb.select(); } }}
];
var cmdFiltered = [];
var cmdActive = 0;

function openCommandPalette(){
  var ov = document.getElementById('cmdPalette');
  if(!ov) return;
  ov.classList.add('show');
  var input = document.getElementById('cmdInput');
  if(input){ input.value=''; input.focus(); }
  renderCmdResults('');
  srAnnounce('已打开命令面板');
}
function closeCommandPalette(){
  var ov = document.getElementById('cmdPalette');
  if(ov) ov.classList.remove('show');
}
function renderCmdResults(q){
  var list = document.getElementById('cmdList');
  if(!list) return;
  q = (q||'').trim().toLowerCase();
  cmdFiltered = CMD_LIST.filter(function(c){
    if(!q) return true;
    return (c.title + ' ' + (c.hint||'')).toLowerCase().indexOf(q) >= 0;
  });
  if(!cmdFiltered.length){
    list.innerHTML = '<div class="cmd-empty">没有匹配的命令</div>';
    return;
  }
  cmdActive = 0;
  list.innerHTML = cmdFiltered.map(function(c,i){
    var svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#'+c.icon+'"/></svg>';
    return '<div class="cmd-item'+(i===0?' active':'')+'" data-i="'+i+'" onmousemove="cmdHover('+i+')" onclick="runCmd('+i+')">'+
      svg + '<div class="cmd-item__body"><div class="cmd-item__title">'+esc(c.title)+'</div>'+(c.hint?'<div class="cmd-item__hint">'+esc(c.hint)+'</div>':'')+'</div></div>';
  }).join('');
}
function cmdHover(i){
  if(i===cmdActive) return;
  cmdActive = i;
  var items = document.querySelectorAll('#cmdList .cmd-item');
  items.forEach(function(el){ el.classList.toggle('active', parseInt(el.dataset.i,10)===i); });
}
function runCmd(i){
  var c = cmdFiltered[i];
  if(!c) return;
  closeCommandPalette();
  try{ c.run(); }catch(e){ console.error('command run error', e); }
}
// 命令面板输入（OPT-06：去抖，避免每次按键重建结果列表 DOM）
var cmdInputTimer = null;
function onCmdInput(){
  if(cmdInputTimer) clearTimeout(cmdInputTimer);
  var v = document.getElementById('cmdInput').value;
  cmdInputTimer = setTimeout(function(){ renderCmdResults(v); }, 120);
}
function onCmdKeydown(e){
  var items = document.querySelectorAll('#cmdList .cmd-item');
  if(e.key === 'Escape'){ e.preventDefault(); closeCommandPalette(); return; }
  if(e.key === 'ArrowDown'){ e.preventDefault(); if(items.length){ cmdActive=(cmdActive+1)%items.length; cmdHover(cmdActive); items[cmdActive].scrollIntoView({block:'nearest'}); } return; }
  if(e.key === 'ArrowUp'){ e.preventDefault(); if(items.length){ cmdActive=(cmdActive-1+items.length)%items.length; cmdHover(cmdActive); items[cmdActive].scrollIntoView({block:'nearest'}); } return; }
  if(e.key === 'Enter'){ e.preventDefault(); runCmd(cmdActive); return; }
}

// ============ 轻量响应缓存（OPT-07） ============
// 以 URL 为 key 缓存只读接口响应，TTL 内复用同一响应、并发请求复用同一 Promise，
// 避免重复请求（双概览 UI 拉取 /api/dashboard/all 等场景的去重在此实现）。
// 仅用于读多写少、短时陈旧可接受的概览类只读接口；请求失败不缓存，下次调用重试。
var _apiCache = {};
function cachedApiGet(url, ttl, force){
  ttl = (ttl == null) ? 10000 : ttl;
  force = !!force;
  var now = Date.now();
  var slot = _apiCache[url];
  if(!force && slot && slot.inflight){ return slot.inflight; }
  if(!force && slot && slot.data != null && (now - slot.ts) < ttl){ return Promise.resolve(slot.data); }
  var p = (async function(){
    try{
      var d = await apiGet(url);
      _apiCache[url] = { ts: Date.now(), data: d, inflight: null };
      return d;
    }catch(e){
      _apiCache[url] = { ts: 0, data: null, inflight: null };
      throw e;
    }
  })();
  _apiCache[url] = { ts: now, data: null, inflight: p };
  return p;
}

// ============ 统一空 / 错误态（OPT-33） ============
// 沿用 history.js 的 _makeEmptyState 结构与 .empty-state 样式，供各模块复用，
// 统一「暂无数据 / 加载失败」的视觉与交互（含重试按钮）。
function renderEmptyState(opts){
  opts = opts || {};
  var icon = opts.icon || 'icon-inbox';
  var title = opts.title || '暂无数据';
  var desc = (opts.desc != null) ? opts.desc : '';
  var action = opts.action || null; // {text, onClick}
  var wrapper = document.createElement('div');
  wrapper.className = 'empty-state';
  var iconDiv = document.createElement('div');
  iconDiv.className = 'empty-icon';
  iconDiv.innerHTML = '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><use href="#'+icon+'"/></svg>';
  var titleDiv = document.createElement('div');
  titleDiv.className = 'empty-title';
  titleDiv.textContent = title;
  wrapper.appendChild(iconDiv);
  wrapper.appendChild(titleDiv);
  if(desc){
    var descDiv = document.createElement('div');
    descDiv.className = 'empty-desc';
    descDiv.innerHTML = desc;
    wrapper.appendChild(descDiv);
  }
  if(action && action.text){
    var btn = document.createElement('button');
    btn.className = 'btn btn-sm btn-outline';
    btn.style.marginTop = '10px';
    btn.textContent = action.text;
    if(action.onClick) btn.addEventListener('click', action.onClick);
    wrapper.appendChild(btn);
  }
  return wrapper;
}

function renderErrorState(opts){
  opts = opts || {};
  var title = opts.title || '加载失败';
  var desc = (opts.desc != null) ? opts.desc : '';
  var onRetry = opts.onRetry || null;
  var wrapper = document.createElement('div');
  wrapper.className = 'empty-state';
  var iconDiv = document.createElement('div');
  iconDiv.className = 'empty-icon';
  iconDiv.style.color = 'var(--red)';
  iconDiv.innerHTML = '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><use href="#icon-x-circle"/></svg>';
  var titleDiv = document.createElement('div');
  titleDiv.className = 'empty-title';
  titleDiv.textContent = title;
  wrapper.appendChild(iconDiv);
  wrapper.appendChild(titleDiv);
  if(desc){
    var descDiv = document.createElement('div');
    descDiv.className = 'empty-desc';
    descDiv.innerHTML = desc;
    wrapper.appendChild(descDiv);
  }
  if(onRetry){
    var btn = document.createElement('button');
    btn.className = 'btn btn-sm btn-primary';
    btn.style.marginTop = '10px';
    btn.textContent = '重试';
    btn.addEventListener('click', onRetry);
    wrapper.appendChild(btn);
  }
  return wrapper;
}
