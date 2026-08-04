// ============ Dashboard / 运行概览 ============
// 状态管理：loading / data / error，所有子组件均从 state 派生渲染
var dashboardState = { loading:true, data:null, error:null };

/**
 * 加载概览数据（入口）。保持函数名 loadDashboard 以兼容 init.js / SSE 调用。
 */
async function loadDashboard(){
  try{
    dashboardState.loading = true;
    var d = await apiGet('/api/dashboard/all');
    dashboardState.data = d;
    dashboardState.error = null;
    renderOverview(d);
    if(d.version) document.getElementById('headerVersion').textContent='v'+d.version;
  }catch(e){
    dashboardState.error = e;
  }finally{
    dashboardState.loading = false;
    setOverviewSkeleton(false);
  }
}

/**
 * 手动刷新：显示骨架屏后重新拉取。
 */
function refreshOverview(){
  setOverviewSkeleton(true);
  loadDashboard();
}

/**
 * 切换骨架屏与真实内容。
 */
function setOverviewSkeleton(show){
  var sk = document.getElementById('overviewSkeleton');
  var ct = document.getElementById('overviewContent');
  if(sk) sk.style.display = show ? 'grid' : 'none';
  if(ct) ct.style.display = show ? 'none' : 'grid';
}

/**
 * 总渲染：将聚合数据分发给各子组件。
 */
function renderOverview(d){
  var s = d.schedule_status || {};
  var stats = d.stats || {};
  renderToday(stats.today_count);
  renderSchedule(s);
  renderStatus(stats.last_status, stats.last_time);
  renderWeekChart(stats);
}

/**
 * 今日转存卡片。
 */
function renderToday(count){
  var el = document.getElementById('ovToday');
  if(!el) return;
  if(count) animateNumber(el, count, 800);
  else el.textContent = '-';
}

/**
 * 下次调度卡片：转存 / 检测两行对齐展示。
 */
function renderSchedule(s){
  var el = document.getElementById('ovSchedule');
  if(!el) return;
  el.textContent = '';
  var rows = [];
  if(s.transfer_next){
    rows.push({icon:'#icon-cloud-download', name:'转存', time:s.transfer_next.slice(5)});
  }
  if(s.expired_check_next){
    rows.push({icon:'#icon-refresh', name:'检测', time:s.expired_check_next.slice(5)});
  }
  if(!rows.length){
    el.textContent = '暂无调度';
    return;
  }
  rows.forEach(function(r){
    var row = document.createElement('div');
    row.className = 'overview-schedule-row';
    row.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="'+r.icon+'"/></svg><span class="task-name">'+esc(r.name)+'</span><span class="task-time">'+esc(r.time)+'</span>';
    el.appendChild(row);
  });
}

/**
 * 上次状态卡片：根据真实状态动态切换图标、颜色与文字。
 */
function renderStatus(status, time){
  var dotEl = document.getElementById('ovStatusDot');
  var textEl = document.getElementById('ovStatusText');
  var timeEl = document.getElementById('ovStatusTime');
  var iconEl = document.getElementById('ovStatusIcon');
  if(!textEl) return;

  var statusMap = {
    'success': {text:'成功', dotClass:'status-dot', textColor:'green', icon:'#icon-check-circle', iconColor:'var(--green)'},
    'partial': {text:'部分成功', dotClass:'status-dot orange', textColor:'orange', icon:'#icon-alert-circle', iconColor:'var(--orange)'},
    'fail':    {text:'失败', dotClass:'status-dot red', textColor:'red', icon:'#icon-x-circle', iconColor:'var(--red)'},
    'none':    {text:'无新增', dotClass:'', textColor:'', icon:'#icon-check-circle', iconColor:'var(--text3)'}
  };
  var st = statusMap[status] || statusMap['none'];

  if(dotEl){
    dotEl.className = st.dotClass;
    dotEl.style.display = st.dotClass ? 'inline-block' : 'none';
  }
  textEl.textContent = st.text;
  textEl.className = 'overview-card__value ' + st.textColor;
  if(timeEl) timeEl.textContent = time ? '上次转存 '+time.slice(5,16) : '上次转存';
  if(iconEl){
    iconEl.style.setProperty('--ov-icon-color', st.iconColor);
    iconEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="'+st.icon+'"/></svg>';
  }
}

/**
 * 近 7 天趋势卡片：柱状图 + 汇总数字。
 */
function renderWeekChart(stats){
  var chartEl = document.getElementById('ovChart');
  if(!chartEl) return;

  animateNumber(document.getElementById('ovWeekOK'), stats.week_ok||0, 900);
  animateNumber(document.getElementById('ovWeekFail'), stats.week_fail||0, 900);
  animateNumber(document.getElementById('ovWeekTotal'), stats.week_total||0, 1000);

  chartEl.textContent = '';
  if(!stats.daily || !stats.daily.length){
    for(var i=0;i<7;i++){
      var emptyBar = document.createElement('div');
      emptyBar.className = 'overview-bar empty';
      emptyBar.style.height = '5%';
      chartEl.appendChild(emptyBar);
    }
    return;
  }

  var maxTotal = 1;
  stats.daily.forEach(function(d){ if(d.total>maxTotal) maxTotal=d.total; });

  stats.daily.forEach(function(d){
    var bar = document.createElement('div');
    bar.className = 'overview-bar';
    var h = Math.round(d.total/maxTotal*100);
    bar.style.height = (h<5?5:h) + '%';
    bar.title = d.date + '　成功 '+d.ok+' / 失败 '+d.fail;
    if(d.total===0) bar.classList.add('empty');
    else if(d.fail>0 && d.ok===0) bar.classList.add('fail');
    else if(d.fail>0) bar.classList.add('mixed');
    else bar.classList.add('ok');
    chartEl.appendChild(bar);
  });
}

/**
 * 小工具：HTML 转义，防止调度时间等字符串注入。
 */
function esc(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
