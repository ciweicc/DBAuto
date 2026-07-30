// ============ Tab / Navigation ============
var currentTab = 'dashboard';
// 页面容器 id 映射：手动/定时/历史保持原 id（categories/schedule/transfer/history.js 依赖）
var PAGE_IDS = {
  dashboard: 'pageDashboard',
  manual: 'tabManual',
  schedule: 'tabSchedule',
  history: 'tabHistory',
  settings: 'tabSettings'
};
var PAGES = ['dashboard', 'manual', 'schedule', 'history', 'settings'];
var BOTTOM_NAV = ['manual', 'schedule', 'history', 'settings'];

function switchTab(tab){
  currentTab = tab;
  // 页面显隐
  PAGES.forEach(function(p){
    var el = document.getElementById(PAGE_IDS[p]);
    if(el) el.classList.toggle('active', p === tab);
  });
  // 侧边栏联动
  document.querySelectorAll('.side-nav-item').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-tab') === tab);
  });
  // 底部导航联动（dashboard 不在底部栏）
  var navIdx = BOTTOM_NAV.indexOf(tab);
  document.querySelectorAll('.bottom-nav .nav-item').forEach(function(n, i){
    n.classList.toggle('active', i === navIdx);
  });
  // 关闭移动端抽屉
  closeDrawers();
  // 按需加载
  if(tab === 'schedule') loadSchedule();
  if(tab === 'history') loadExecHistory();
  if(tab === 'dashboard') initTmdbPage();
  if(tab === 'settings') loadConfig();
}
