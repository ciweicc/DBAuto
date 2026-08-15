// ============ Tab / Navigation ============
var currentTab = 'overview';
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
  // 概览页时收起日志面板（给内容更多空间）
  if(tab === 'overview') collapseLogPanel();
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
