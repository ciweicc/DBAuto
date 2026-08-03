// ============ Tab / Navigation ============
var currentTab = 'dashboard';
// 页面容器 id 映射：手动/定时/历史保持原 id（categories/schedule/transfer/history.js 依赖）
var PAGE_IDS = {
  dashboard: 'pageDashboard',
  manual: 'tabManual',
  schedule: 'tabSchedule',
  history: 'tabHistory',
  settings: 'tabSettings',
  tmdb: 'pageTmdb'
};
var PAGES = ['dashboard', 'manual', 'schedule', 'history', 'settings', 'tmdb'];
// 底部导航与 BOTTOM_NAV 顺序保持一致：概览 / 转存 / 定时 / 历史 / 设置 / 资源
var BOTTOM_NAV = ['dashboard', 'manual', 'schedule', 'history', 'settings', 'tmdb'];

function switchTab(tab){
  currentTab = tab;
  // 页面显隐
  PAGES.forEach(function(p){
    var el = document.getElementById(PAGE_IDS[p]);
    if(el) el.classList.toggle('active', p === tab);
  });
  // 侧边栏联动（同时标记 aria-current 供屏幕阅读器识别）
  document.querySelectorAll('.side-nav-item').forEach(function(b){
    var on = b.getAttribute('data-tab') === tab;
    b.classList.toggle('active', on);
    if(on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  // 底部导航联动
  var navIdx = BOTTOM_NAV.indexOf(tab);
  document.querySelectorAll('.bottom-nav .nav-item').forEach(function(n, i){
    var on = i === navIdx;
    n.classList.toggle('active', on);
    if(on) n.setAttribute('aria-current', 'page'); else n.removeAttribute('aria-current');
  });
  // 关闭移动端抽屉
  closeDrawers();
  // 按需加载
  if(tab === 'schedule') loadSchedule();
  if(tab === 'history') loadExecHistory();
  if(tab === 'tmdb') initTmdbPage();
  if(tab === 'settings') loadConfig();
}
