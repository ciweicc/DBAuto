// ============ Tab ============
function updateTabIndicator(){
  var tabs = document.getElementById('mainTabs');
  var indicator = document.getElementById('tabIndicator');
  var activeBtn = tabs.querySelector('.tab-btn.active');
  if(!indicator||!activeBtn)return;
  var tabsRect = tabs.getBoundingClientRect();
  var btnRect = activeBtn.getBoundingClientRect();
  indicator.style.width = btnRect.width + 'px';
  indicator.style.transform = 'translateX(' + (btnRect.left - tabsRect.left) + 'px)';
}
function switchTab(tab){
  currentTab = tab;
  var idx = tab==='manual'?0:tab==='schedule'?1:tab==='tmdb'?2:tab==='history'?3:4;
  document.querySelectorAll('.tab-btn').forEach(function(b,i){
    b.classList.toggle('active', i===idx);
  });
  var navItems = document.querySelectorAll('.bottom-nav .nav-item');
  navItems.forEach(function(n,i){
    n.classList.toggle('active', i===idx);
  });
  document.getElementById('tabManual').classList.toggle('active', tab==='manual');
  document.getElementById('tabSchedule').classList.toggle('active', tab==='schedule');
  document.getElementById('tabHistory').classList.toggle('active', tab==='history');
  document.getElementById('tabTmdb').classList.toggle('active', tab==='tmdb');
  document.getElementById('tabSettings').classList.toggle('active', tab==='settings');
  updateTabIndicator();
  if(tab==='schedule')loadSchedule();
  if(tab==='history')loadExecHistory();
  if(tab==='tmdb')initTmdbPage();
  if(tab==='settings')loadConfig();
}

