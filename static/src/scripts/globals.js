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
