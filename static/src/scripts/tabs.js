// ============ Tab / Navigation ============
// 页面容器 id 映射：手动/定时/历史保持原 id（categories/schedule/transfer/history.js 依赖）
var PAGE_IDS = {
  overview: 'pageOverview',
  tmdb: 'pageTmdb',
  manual: 'tabManual',
  schedule: 'tabSchedule',
  history: 'tabHistory',
  settings: 'tabSettings'
};
var PAGES = ['overview', 'tmdb', 'manual', 'schedule', 'history', 'settings'];
// 底部导航与 BOTTOM_NAV 顺序保持一致：概览 / 发现 / 转存 / 定时 / 历史 / 设置
var BOTTOM_NAV = ['overview', 'tmdb', 'manual', 'schedule', 'history', 'settings'];

function switchTab(tab){
  currentTab = tab;
  // 页面显隐
  PAGES.forEach(function(p){
    var el = document.getElementById(PAGE_IDS[p]);
    if(el) el.classList.toggle('active', p === tab);
  });
  // 侧边栏联动（role=tab：同步 aria-selected 与 aria-current）
  document.querySelectorAll('.side-nav-item').forEach(function(b){
    var on = b.getAttribute('data-tab') === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
    if(on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  // 底部导航联动（role=tab：同步 aria-selected，不再使用 aria-current）
  var navIdx = BOTTOM_NAV.indexOf(tab);
  document.querySelectorAll('.bottom-nav .nav-item').forEach(function(n, i){
    var on = i === navIdx;
    n.classList.toggle('active', on);
    n.setAttribute('aria-selected', on ? 'true' : 'false');
    n.removeAttribute('aria-current');
  });
  // 关闭移动端抽屉
  closeDrawers();
  // 按需加载
  if(tab === 'overview') loadOverviewPage();
  if(tab === 'schedule') loadSchedule();
  if(tab === 'history') loadExecHistory();
  if(tab === 'tmdb') initTmdbPage();
  if(tab === 'settings') loadConfig();
  // TMDB "回到顶部"按钮：离开 TMDB 页时隐藏，返回时按当前滚动位置刷新可见性
  if(tab !== 'tmdb') tmdbHideBackToTop();
  else tmdbUpdateBackToTop();
  // 概览页不再自动收起日志面板。
  //
  // 历史缺陷：这里曾以「窄屏 + 用户未显式选择」为条件自动 collapseLogPanel()，
  // 结果是 ≤1200px 打开概览页时，正在跑的任务日志被压成 48px 轨道、一条都看不到
  // （issue #11「侧边栏的实时任务日志无法显示完全」）。
  // 日志栏本身已是常驻功能区（见 410498f / 58fcfd4 的 P0 修复），
  // 「给内容更多空间」应由用户点折叠按钮显式完成，而不是打开页面就默认隐藏。
  // 相应地 FAB / 折叠按钮是该功能的显式出口，无需再靠自动折叠来"提醒"。
}

// 方向键在标签间导航（ARIA tabs 模式：左右 / Home / End 切换并自动激活）
document.addEventListener('keydown', function(e){
  var t = e.target;
  if(!t || !t.classList) return;
  var isTab = t.classList.contains('side-nav-item') || t.classList.contains('nav-item');
  if(!isTab) return;
  if(e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return;
  e.preventDefault();
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.side-nav-item'));
  var idx = tabs.indexOf(t);
  if(idx < 0) return;
  var n = tabs.length;
  if(e.key === 'ArrowRight') idx = (idx + 1) % n;
  else if(e.key === 'ArrowLeft') idx = (idx - 1 + n) % n;
  else if(e.key === 'Home') idx = 0;
  else if(e.key === 'End') idx = n - 1;
  var next = tabs[idx].getAttribute('data-tab');
  switchTab(next);
  tabs[idx].focus();
});

// 日志面板：窄屏判定 + 用户显式选择记忆
function isNarrowViewport(){
  return window.matchMedia && window.matchMedia('(max-width:1200px)').matches;
}
function userSetLogPanelState(){
  try{ return localStorage.getItem('logPanelCollapsed') !== null; }catch(e){ return false; }
}
// 首屏恢复折叠态：默认（无记忆）为展开。
//
// 历史上这里「用户未显式选择时自动 collapse」，叠加 ≤1200px 的窄屏判定后，
// 窄屏/移动端一打开页面日志栏就是 48px 折叠轨道 —— 正在运行的任务日志
// 一条都看不见（issue #11）。现在只有用户自己点过折叠按钮才恢复折叠。
function restoreLogPanelState(){
  var panel = document.getElementById('logPanel');
  if(!panel) return;
  var saved = null;
  try{ saved = localStorage.getItem('logPanelCollapsed'); }catch(e){ saved = null; }
  if(saved === '1') collapseLogPanel();
  else {
    if(panel.classList.contains('collapsed')) panel.classList.remove('collapsed');
    _syncLogPanelA11y();
  }
}
