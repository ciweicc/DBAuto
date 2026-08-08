// ============ Global ============
var C = {}, currentTab = 'manual', execHistoryData = [], execHistoryFilter = 'all';
var expiredDirEntries = [], autoSaveTimer = null, logPollTimer = null;
var logBefore = [], logFilter = 'all', logPaused = false;
var logPollInterval = 0;
var sseConnected = false;
var LOG_BEFORE_MAX = 500;
var APP_PATHS = {category_base:'/影视', search:'/批量转存/手动搜索存', tmdb:'/批量转存/TMDB'};
var SETTINGS_ALL = null;  // 缓存 /api/settings/all，调度页与设置页共用，避免重复请求



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
  {title:'前往 TMDB 资源浏览', icon:'icon-tmdb', hint:'浏览 / 转存影视', run:function(){ switchTab('tmdb'); }},
  {title:'前往 手动转存', icon:'icon-send', hint:'', run:function(){ switchTab('manual'); }},
  {title:'前往 定时任务', icon:'icon-clock', hint:'', run:function(){ switchTab('schedule'); }},
  {title:'前往 执行历史', icon:'icon-check-circle', hint:'', run:function(){ switchTab('history'); }},
  {title:'前往 设置', icon:'icon-settings', hint:'', run:function(){ switchTab('settings'); }},
  {title:'开始转存（手动页）', icon:'icon-transfer', hint:'Ctrl/⌘ + Enter', run:function(){ if(currentTab==='manual'){ var s=document.getElementById('stopBtn'); if(!(s&&s.style.display!=='none')) startTransfer(); } }},
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
function onCmdInput(){
  renderCmdResults(document.getElementById('cmdInput').value);
}
function onCmdKeydown(e){
  var items = document.querySelectorAll('#cmdList .cmd-item');
  if(e.key === 'Escape'){ e.preventDefault(); closeCommandPalette(); return; }
  if(e.key === 'ArrowDown'){ e.preventDefault(); if(items.length){ cmdActive=(cmdActive+1)%items.length; cmdHover(cmdActive); items[cmdActive].scrollIntoView({block:'nearest'}); } return; }
  if(e.key === 'ArrowUp'){ e.preventDefault(); if(items.length){ cmdActive=(cmdActive-1+items.length)%items.length; cmdHover(cmdActive); items[cmdActive].scrollIntoView({block:'nearest'}); } return; }
  if(e.key === 'Enter'){ e.preventDefault(); runCmd(cmdActive); return; }
}
