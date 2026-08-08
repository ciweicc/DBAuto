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
