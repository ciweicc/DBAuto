// ============ Overview Page（概览首页） ============
var ovState = { loaded:false, loading:false };

/**
 * 加载概览页（入口，每次切到概览 Tab 时调用）
 */
async function loadOverviewPage(){
  if(ovState.loading) return;
  ovState.loading = true;
  try{
    // OPT-03：/api/dashboard/all 与 dashboard.js 的 overview-grid 共用同一份缓存数据，
    // 避免双概览 UI 重复拉取；/api/history 与 TMDB 推荐以轻量缓存去重（OPT-07）。
    var results = await Promise.allSettled([
      fetchDashboardAll(false),
      cachedApiGet('/api/history', 15000, false),
      cachedApiGet('/api/tmdb/list?media_type=movie&list_type=trending&page=1', 60000, false)
    ]);
    var dash = results[0].status === 'fulfilled' ? results[0].value : null;
    var hist = results[1].status === 'fulfilled' ? results[1].value : null;
    var tmdb = results[2].status === 'fulfilled' ? results[2].value : null;

    renderOvKpi(dash, hist);
    renderOvRecent(hist);
    renderOvHealth(dash);
    renderOvTodos(dash, hist);
    renderOvRecs(tmdb);
    ovState.loaded = true;
  }catch(e){
    // 静默失败
  }finally{
    ovState.loading = false;
  }
}

/**
 * 顶部 KPI 条
 */
function renderOvKpi(dash, hist){
  var stats = (dash && dash.stats) || {};
  var sched = (dash && dash.schedule_status) || {};

  // 今日转存
  var todayCount = stats.today_count || 0;
  var todayEl = document.getElementById('ovTodayCount');
  if(todayEl) animateNumber(todayEl, todayCount, 600);
  var todaySub = document.getElementById('ovTodaySub');
  if(todaySub){
    if(todayCount > 0){
      todaySub.textContent = '近7天成功 ' + (stats.week_ok||0) + ' · 失败 ' + (stats.week_fail||0);
    } else {
      todaySub.textContent = '今日暂无转存';
    }
  }

  // 7天成功率
  var weekTotal = stats.week_total || 0;
  var weekOk = stats.week_ok || 0;
  var rate = weekTotal > 0 ? Math.round(weekOk / (weekOk + (stats.week_fail||0)) * 100) : 0;
  if(weekOk === 0 && (stats.week_fail||0) === 0) rate = 0;
  var rateEl = document.getElementById('ovWeekRate');
  if(rateEl) rateEl.textContent = rate + '%';
  var barEl = document.getElementById('ovWeekBar');
  if(barEl) barEl.style.width = rate + '%';
  var trendEl = document.getElementById('ovWeekTrend');
  if(trendEl) trendEl.textContent = weekTotal + ' 次/周';

  // 下次调度
  var schedEl = document.getElementById('ovNextSchedule');
  if(schedEl){
    schedEl.innerHTML = '';
    var rows = [];
    if(sched.transfer_next){
      rows.push({name:'转存', time:formatOvTime(sched.transfer_next)});
    }
    if(sched.expired_check_next){
      rows.push({name:'检测', time:formatOvTime(sched.expired_check_next)});
    }
    if(!rows.length){
      schedEl.innerHTML = '<span class="ov-kpi-muted">暂无调度</span>';
    } else {
      rows.forEach(function(r){
        var row = document.createElement('div');
        row.className = 'ov-kpi-sched-row';
        row.innerHTML = '<span class="ov-kpi-sched-name">' + esc(r.name) + '</span><span class="ov-kpi-sched-time">' + esc(r.time) + '</span>';
        schedEl.appendChild(row);
      });
    }
  }

  // 转存库总数
  var totalItems = (hist && hist.total) || 0;
  var pendEl = document.getElementById('ovPendingCount');
  if(pendEl) animateNumber(pendEl, totalItems, 600);
  var pendSub = document.getElementById('ovPendingSub');
  if(pendSub){
    var lastStatus = stats.last_status;
    var statusText = '一切正常';
    if(lastStatus === 'success') statusText = '上次转存成功';
    else if(lastStatus === 'partial') statusText = '上次部分成功';
    else if(lastStatus === 'fail') statusText = '上次转存失败';
    pendSub.textContent = statusText;
  }
}

function formatOvTime(t){
  if(!t) return '';
  // "2024-01-01 12:00:00" → "01-01 12:00"
  try{ return t.slice(5, 16); }catch(e){ return t; }
}

/**
 * 最近转存列表
 */
function renderOvRecent(hist){
  var tbody = document.getElementById('ovRecentBody');
  if(!tbody) return;
  var itemsObj = (hist && hist.items) || {};
  // items 是 {title: {category, shareurl, date, ...}} 对象，转为数组并按日期降序
  var items = [];
  for(var title in itemsObj){
    if(itemsObj.hasOwnProperty(title)){
      var info = itemsObj[title] || {};
      items.push({
        title: title,
        category: info.category || '-',
        date: info.date || '',
        shareurl: info.shareurl || ''
      });
    }
  }
  items.sort(function(a,b){ return (b.date||'').localeCompare(a.date||''); });

  if(!items.length){
    tbody.innerHTML = '<tr><td colspan="4" class="ov-table-empty">暂无转存记录</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  items.slice(0, 8).forEach(function(item){
    var tr = document.createElement('tr');
    var dateShort = item.date ? item.date.slice(5, 16) : '-';
    tr.innerHTML =
      '<td class="ov-table-title"><span class="ov-table-cat">' + esc(item.category) + '</span>' + esc(item.title.length > 30 ? item.title.slice(0,30)+'…' : item.title) + '</td>' +
      '<td class="ov-table-date">' + esc(dateShort) + '</td>' +
      '<td><span class="ov-badge ov-badge-success">已转存</span></td>' +
      '<td class="ov-table-actions">' +
        (item.shareurl ? '<a href="' + esc(item.shareurl) + '" target="_blank" rel="noopener" class="ov-btn-icon" title="打开链接"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>' : '') +
      '</td>';
    tbody.appendChild(tr);
  });
}

/**
 * 系统健康状态
 */
function renderOvHealth(dash){
  var el = document.getElementById('ovHealthList');
  if(!el) return;
  var sched = (dash && dash.schedule_status) || {};
  var stats = (dash && dash.stats) || {};
  var version = (dash && dash.version) || '';

  var checks = [];

  // 调度器状态
  var schedRunning = !!(sched.transfer_next || sched.expired_check_next);
  checks.push({
    label: '定时调度',
    status: schedRunning ? 'ok' : 'warn',
    text: schedRunning ? '运行中' : '未配置'
  });

  // 最近转存状态
  var lastStatus = stats.last_status || '-';
  checks.push({
    label: '最近转存',
    status: lastStatus === 'success' ? 'ok' : (lastStatus === 'fail' ? 'error' : (lastStatus === 'partial' ? 'warn' : 'ok')),
    text: lastStatus === 'success' ? '成功' : (lastStatus === 'fail' ? '失败' : (lastStatus === 'partial' ? '部分成功' : '无记录'))
  });

  // 版本
  checks.push({
    label: '系统版本',
    status: 'ok',
    text: 'v' + version
  });

  el.innerHTML = '';
  checks.forEach(function(c){
    var dot = c.status === 'ok' ? 'ov-dot-ok' : (c.status === 'warn' ? 'ov-dot-warn' : 'ov-dot-error');
    var row = document.createElement('div');
    row.className = 'ov-health-row';
    row.innerHTML = '<span class="ov-dot ' + dot + '"></span><span class="ov-health-label">' + esc(c.label) + '</span><span class="ov-health-value">' + esc(c.text) + '</span>';
    el.appendChild(row);
  });
}

/**
 * 待办事项
 */
function renderOvTodos(dash, hist){
  var el = document.getElementById('ovTodoList');
  if(!el) return;
  var todos = [];
  var stats = (dash && dash.stats) || {};
  var sched = (dash && dash.schedule_status) || {};

  // 上次转存失败提醒
  if(stats.last_status === 'fail'){
    todos.push({icon:'alert', text:'上次转存存在失败项，建议检查日志', action:'switchTab(\'history\')', actionText:'查看历史'});
  } else if(stats.last_status === 'partial'){
    todos.push({icon:'alert', text:'上次转存部分失败，建议检查', action:'switchTab(\'history\')', actionText:'查看详情'});
  }

  // 调度未配置提醒
  if(!sched.transfer_next && !sched.expired_check_next){
    todos.push({icon:'clock', text:'定时任务未配置，建议设置自动转存', action:'switchTab(\'schedule\')', actionText:'去配置'});
  }

  // 今日无转存
  if((stats.today_count||0) === 0){
    todos.push({icon:'send', text:'今日还没有转存记录', action:'switchTab(\'tmdb\')', actionText:'去发现'});
  }

  if(!todos.length){
    todos.push({icon:'check', text:'系统运行正常，暂无待办', action:'', actionText:''});
  }

  el.innerHTML = '';
  todos.forEach(function(t){
    var item = document.createElement('div');
    item.className = 'ov-todo-item';
    var iconSvg = t.icon === 'alert' ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
      : t.icon === 'clock' ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
      : t.icon === 'send' ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
    item.innerHTML = '<span class="ov-todo-icon ov-todo-icon-' + t.icon + '">' + iconSvg + '</span><span class="ov-todo-text">' + esc(t.text) + '</span>' + (t.action ? '<button class="ov-todo-btn" onclick="' + t.action + '">' + esc(t.actionText) + '</button>' : '');
    el.appendChild(item);
  });
}

/**
 * 热门推荐横滑
 */
function renderOvRecs(tmdb){
  var el = document.getElementById('ovRecScroll');
  if(!el) return;
  var items = [];
  if(tmdb){
    if(tmdb.results && Array.isArray(tmdb.results)) items = tmdb.results.slice(0, 12);
    else if(tmdb.items && Array.isArray(tmdb.items)) items = tmdb.items.slice(0, 12);
    else if(Array.isArray(tmdb)) items = tmdb.slice(0, 12);
  }

  if(!items.length){
    el.innerHTML = '<div class="ov-rec-empty">暂无推荐数据</div>';
    return;
  }

  el.innerHTML = '';
  items.forEach(function(item){
    var title = item.title || item.name || item.original_title || item.original_name || '未知';
    var rating = item.vote_average || item.rating || 0;
    var poster = item.poster_path || item.poster || '';
    var date = item.release_date || item.first_air_date || '';
    var year = date ? date.slice(0,4) : '';

    var card = document.createElement('div');
    card.className = 'ov-rec-card';
    card.onclick = function(){ switchTab('tmdb'); };
    var fallbackHtml =
      '<div class="ov-rec-poster-fallback"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>';
    var posterHtml = poster
      ? '<img class="ov-rec-img" src="https://image.tmdb.org/t/p/w185' + esc(poster) + '" alt="' + esc(title) + '" loading="lazy" onerror="this.style.display=\'none\'">' + fallbackHtml
      : fallbackHtml;
    card.innerHTML =
      '<div class="ov-rec-poster">' + posterHtml + '</div>' +
      '<div class="ov-rec-info">' +
        '<div class="ov-rec-title" title="' + esc(title) + '">' + esc(title) + '</div>' +
        '<div class="ov-rec-meta">' + (year ? esc(year) + ' · ' : '') + (rating ? '★ ' + Number(rating).toFixed(1) : '') + '</div>' +
      '</div>';
    el.appendChild(card);
  });
}

/**
 * 立即运行定时任务
 */
async function runScheduleNow(){
  try{
    await apiPost('/api/schedule', {action:'run_now'});
    showToast('已触发定时任务', true);
    loadOverviewPage();
  }catch(e){
    showToast('触发失败: ' + (e.message || '未知错误'), false);
  }
}
