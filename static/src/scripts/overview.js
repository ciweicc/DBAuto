// ============ Overview Page（概览首页） ============
// 概览页的目标是「一屏内给出结论」：核心指标、最近转存、需要关注的状态。
// 因此首屏只放三类信息，明细走各自的 Tab（历史 / 设置 / TMDB）。

var ovState = { loaded:false, loading:false, hasData:false };

// 概览列表：最多渲染 10 行（面板高度固定约 540px，10 行 ≈ 370px + 表头 + 脚注），
// 更多走「查看全部 → 历史记录」，避免「最近 8 条 / 查看全部 / 面板内滚动」三重截断。
var OV_RECENT_LIMIT = 10;

// 主列宽度低于该值时，概览内部的「指标 | 状态」两列改为堆叠。
// 用主列实测宽度而非 viewport 断点：侧栏/日志栏/滚动条都会改变主列可用宽度，
// 用 media query 判断会在 1280~1440 这类中间宽度上误判（见 docs/UI_Roadmap_Next.md 第十节）。
var OV_NARROW_PX = 860;

/**
 * 加载概览页（入口，每次切到概览 Tab 时调用）
 */
async function loadOverviewPage(){
  if(ovState.loading) return;
  ovState.loading = true;
  try{
    // 首载先铺骨架，避免数字从 "-" 跳到真实值造成布局抖动
    if(!ovState.hasData) renderOvSkeletons();
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
    ovState.hasData = true;
  }catch(e){
    // 静默失败
  }finally{
    ovState.loading = false;
  }
}

/**
 * 首载骨架屏（3.2）：结构、行高与真实内容一致，避免加载完成的「跳变」
 */
function renderOvSkeletons(){
  var tbody = document.getElementById('ovRecentBody');
  if(tbody){
    var rows = '';
    for(var i = 0; i < 8; i++){
      rows += '<tr class="ov-skel-row">' +
        '<td><span class="ov-skel ov-skel-title"></span></td>' +
        '<td><span class="ov-skel ov-skel-sm"></span></td>' +
        '<td><span class="ov-skel ov-skel-md"></span></td>' +
        '<td><span class="ov-skel ov-skel-dot"></span></td>' +
        '<td><span class="ov-skel ov-skel-dot"></span></td>' +
        '</tr>';
    }
    tbody.innerHTML = rows;
  }
  var rec = document.getElementById('ovRecScroll');
  if(rec){
    var cards = '';
    for(var j = 0; j < 7; j++){
      cards += '<div class="ov-rec-skel"><span class="ov-skel ov-skel-poster"></span><span class="ov-skel ov-skel-line"></span></div>';
    }
    rec.innerHTML = cards;
  }
}

/**
 * 概览头部副信息 + 「指标 | 状态」两列的自适应切换。
 *
 * 不使用 `@media(max-width:...)`：Playwright 的 viewport 宽度与 `window.innerWidth`
 * 会因滚动条相差十余像素，且侧栏折叠、日志栏宽度都会改变主列宽度，
 * 结果在 1280~1440 区间出现「媒体查询命中了但空间其实够用」的堆叠。
 * 改为监听主列实测宽度，用它设置数据属性。
 */
function initOverviewViewport(){
  var app = document.querySelector('.app');
  var main = document.querySelector('.main');
  if(!app || !main) return;

  function apply(w){
    var narrow = w > 0 && w < OV_NARROW_PX;
    app.setAttribute('data-ov-narrow', narrow ? '1' : '0');
  }
  apply(main.getBoundingClientRect().width);

  if(typeof ResizeObserver === 'undefined') return;
  if(ovState._ro) ovState._ro.disconnect();
  ovState._ro = new ResizeObserver(function(entries){
    for(var i = 0; i < entries.length; i++){
      apply(entries[i].contentRect.width);
    }
  });
  ovState._ro.observe(main);
}

/**
 * 顶部指标区：今日转存 / 近 7 天成功率 / 下次调度
 *
 * 与旧 KPI 条的区别（issue #8）：
 * - 四张等宽卡片里「转存库总数」与「最近转存」面板标题重复，已移除，
 *   改为在面板副标题里表达（信息只出现一次，且不再挤占指标行的宽度）；
 * - 「成功 X · 失败 Y」原本挂在「今日转存」下（语义不符），改挂到成功率行；
 * - 每行只保留一个动作按钮，减少首屏的视觉噪声。
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
    todaySub.textContent = todayCount > 0
      ? '共 ' + (stats.week_ok || 0) + ' 次成功'
      : '今日暂无转存';
  }

  // 近 7 天成功率：分母只取「成功 + 失败」，跳过项（幂等转存）不计入
  var weekOk = stats.week_ok || 0;
  var weekFail = stats.week_fail || 0;
  var denom = weekOk + weekFail;
  var rate = denom > 0 ? Math.round(weekOk / denom * 100) : 0;
  var rateEl = document.getElementById('ovWeekRate');
  if(rateEl) rateEl.textContent = rate + '%';
  var barEl = document.getElementById('ovWeekBar');
  if(barEl) barEl.style.width = rate + '%';
  var rateLabel = document.getElementById('ovWeekRateLabel');
  if(rateLabel){
    rateLabel.textContent = denom > 0
      ? '成功 ' + weekOk + ' · 失败 ' + weekFail
      : '本周期无转存结论';
  }
  var rateSub = document.getElementById('ovWeekRateSub');
  if(rateSub) rateSub.textContent = (stats.week_total || 0) + ' 次/周';

  // 下次调度
  var schedEl = document.getElementById('ovNextSchedule');
  if(schedEl){
    var parts = [];
    if(sched.transfer_next) parts.push('转存 ' + fmtNextTime(sched.transfer_next));
    if(sched.expired_check_next) parts.push('检测 ' + fmtNextTime(sched.expired_check_next));
    if(!parts.length){
      schedEl.innerHTML = '<span class="ov-kpi-muted">未配置</span>';
    }else{
      schedEl.textContent = parts.join(' · ');
    }
  }

  // 概览头部副信息（原「转存库」卡片的职责）
  var subEl = document.getElementById('ovOverviewSub');
  if(subEl){
    var total = (hist && hist.total) || 0;
    subEl.textContent = '转存库 ' + total + ' 条 · ' + ovLastStatusText(stats.last_status);
  }
}

/** 上次转存状态文案（概览头部与健康列表共用） */
function ovLastStatusText(st){
  if(st === 'success') return '上次成功';
  if(st === 'partial') return '上次部分成功';
  if(st === 'fail') return '上次失败';
  if(st === 'none') return '上次无有效结果';
  return '暂无执行记录';
}

// OPT-35：本地 formatOvTime 已统一为全局 fmtNextTime（见 globals.js），此处不再保留副本。

/**
 * 转存条目状态：区分「本次产生链接」与「已存在被跳过」。
 *
 * 存储里 status 为 `exists`（幂等跳过）或 `ok/downloading/invalid/error`，
 * 单条转存时同一份 status 也写进 transfer_history。
 * 旧实现把每行都渲染成绿色「已转存」，会把跳过误报成成功。
 * 缺失/未知状态时回退看 shareurl 是否存在，最后才落到「已入库」。
 */
function ovRecentState(item){
  var hasUrl = !!(item && item.shareurl);
  var st = (item && item.status) ? String(item.status) : '';
  if(st === 'ok' || st === 'done'){
    return {cls:'ov-badge-success', text:'已转存', hint:'本次产生了新的分享链接'};
  }
  if(st === 'exists' || st === 'skipped'){
    return {cls:'ov-badge-skip', text:'已存在跳过', hint:'幂等转存：资源已在库中，未产生新链接'};
  }
  if(st === 'fail' || st === 'error' || st === 'invalid'){
    return {cls:'ov-badge-fail', text:'失败', hint:'本次转存未成功，可在历史记录中查看详情'};
  }
  if(st === 'downloading' || st === 'running'){
    return {cls:'ov-badge-run', text:'进行中', hint:'转存仍在进行中'};
  }
  return hasUrl
    ? {cls:'ov-badge-success', text:'已转存', hint:'已有分享链接'}
    : {cls:'ov-badge-muted', text:'已入库', hint:'已记录在转存历史中'};
}

/**
 * 最近转存列表
 *
 * 修复（issue #8）：
 * 1. 渲染上限由硬编码 8 条提升到 OV_RECENT_LIMIT，与「查看全部」的关系不再是三重截断；
 * 2. 日期做空值保护（存储里的 date 可能为空串），避免空值参与比较后把条目排到最前；
 * 3. 「库内」列直接表达幂等跳过，而不是让用户从空链接列去猜。
 */
function renderOvRecent(hist){
  var tbody = document.getElementById('ovRecentBody');
  if(!tbody) return;
  var itemsObj = (hist && hist.items) || {};
  // items 是 {title: {category, shareurl, date, status, ...}} 对象，转为数组并按日期降序
  var items = [];
  for(var title in itemsObj){
    if(itemsObj.hasOwnProperty(title)){
      var info = itemsObj[title] || {};
      items.push({
        title: title,
        category: info.category || '-',
        date: info.date || '',
        shareurl: info.shareurl || '',
        status: info.status || ''
      });
    }
  }
  // 缺失日期的条目排在最后（历史上它们会因为空串比较而插到列表最前）
  items.sort(function(a,b){
    var da = a.date || '', db = b.date || '';
    if(da && !db) return -1;
    if(!da && db) return 1;
    return db.localeCompare(da);
  });

  var total = (hist && hist.total);
  if(typeof total !== 'number') total = items.length;
  var subEl = document.getElementById('ovRecentSub');
  if(subEl) subEl.textContent = '最近 ' + Math.min(items.length, OV_RECENT_LIMIT) + ' 条 / 共 ' + total + ' 条';

  if(!items.length){
    tbody.innerHTML = '<tr><td colspan="5" class="ov-table-empty">暂无转存记录</td></tr>';
    return;
  }
  tbody.innerHTML = '';
  items.slice(0, OV_RECENT_LIMIT).forEach(function(item){
    var tr = document.createElement('tr');
    // 2.3 移动端表格卡片化：td 携带 data-label，<640px 时由 CSS 渲染为字段行
    tr.innerHTML =
      '<td class="ov-table-title"><span class="ov-table-cat">' + esc(ovCategoryLabel(item.category)) + '</span>' + esc(item.title) + '</td>' +
      '<td class="ov-table-date" data-label="时间">' + esc(ovDateShort(item.date)) + '</td>' +
      '<td data-label="状态">' + ovStateBadgeHtml(item) + '</td>' +
      '<td class="ov-table-lib" data-label="库内">' + (ovInLibrary(item) ? '<span class="ov-lib-ok" title="已在转存历史中">是</span>' : '<span class="ov-table-nolink">—</span>') + '</td>' +
      '<td class="ov-table-actions" data-label="链接">' +
        (item.shareurl
          ? '<a href="' + esc(item.shareurl) + '" target="_blank" rel="noopener" class="ov-btn-icon" title="打开分享链接" aria-label="打开「' + esc(item.title) + '」的分享链接"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>'
          : '<span class="ov-table-nolink">—</span>') +
      '</td>';
    tbody.appendChild(tr);
  });
}

/** 状态徽标 HTML（概览列表专用，跳过/失败不再一律显示为「已转存」） */
function ovStateBadgeHtml(item){
  var st = ovRecentState(item);
  return '<span class="ov-badge ' + st.cls + '" title="' + esc(st.hint) + '">' + esc(st.text) + '</span>';
}

/** 历史条目是否已在库内：有链接或状态已落库均视为已入库 */
function ovInLibrary(item){
  return !!(item.shareurl) || !!item.status;
}

/** 日期显示：2026-09-15 10:00 → 09/15 10:00；空值显示 —（不参与排序，也不显示「-」以外的怪值） */
function ovDateShort(date){
  if(!date) return '—';
  var s = String(date).replace('T', ' ');
  if(s.length < 16) return s;
  return s.slice(5, 7) + '/' + s.slice(8, 10) + ' ' + s.slice(11, 16);
}

/** 分类文案：movie/tv/variety → 电影/剧集/综艺 */
function ovCategoryLabel(cat){
  if(cat === 'movie') return '电影';
  if(cat === 'tv') return '剧集';
  if(cat === 'variety') return '综艺';
  return cat || '-';
}

/**
 * 系统健康状态
 *
 * 「需要关注」的检查项（未配置调度 / 上次失败）展开为独立行；
 * 正常的检查项压成一行「其余 N 项正常」，把右列的高度让给待办与最近转存。
 */
function renderOvHealth(dash){
  var el = document.getElementById('ovHealthList');
  if(!el) return;
  var sched = (dash && dash.schedule_status) || {};
  var stats = (dash && dash.stats) || {};
  var version = (dash && dash.version) || '';

  var schedRunning = !!(sched.transfer_next || sched.expired_check_next);
  var lastStatus = stats.last_status || '-';

  var alerts = [];
  if(!schedRunning) alerts.push({label:'定时调度', status:'warn', text:'未配置'});
  if(lastStatus === 'fail') alerts.push({label:'最近转存', status:'error', text:'失败'});
  else if(lastStatus === 'partial') alerts.push({label:'最近转存', status:'warn', text:'部分成功'});

  var okCount = 3 - alerts.length;   // 定时调度 / 最近转存 / 系统版本 三项
  var rows = alerts.slice();
  rows.push({
    label: alerts.length ? '其余 ' + okCount + ' 项' : '运行状态',
    status: 'ok',
    text: alerts.length ? '正常' : '全部正常 · v' + version
  });

  el.innerHTML = '';
  rows.forEach(function(c){
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
 *
 * 数据源是 TMDB 列表接口（字段 poster/title/rating/year），
 * 旧实现读的是 poster_path/release_date/vote_average 等上游原始字段，
 * 结果每张卡都退化成 36px 的占位图标。此处按接口契约取字段。
 */
function renderOvRecs(tmdb){
  var el = document.getElementById('ovRecScroll');
  if(!el) return;
  var items = [];
  if(tmdb){
    if(Array.isArray(tmdb.items)) items = tmdb.items.slice(0, 12);
    else if(Array.isArray(tmdb.results)) items = tmdb.results.slice(0, 12);
    else if(Array.isArray(tmdb)) items = tmdb.slice(0, 12);
  }

  if(!items.length){
    el.innerHTML = '<div class="ov-rec-empty">暂无推荐数据</div>';
    return;
  }

  el.innerHTML = '';
  items.forEach(function(item){
    var title = item.title || item.name || item.original_title || item.original_name || '未知';
    var rating = item.rating || item.vote_average || 0;
    var poster = item.poster || item.poster_path || '';
    var src = /^https?:/.test(poster) ? poster : (poster ? TMDB_IMG_BASE + poster : '');
    var year = item.year || (item.release_date || item.first_air_date || '').slice(0, 4);

    var card = document.createElement('div');
    card.className = 'ov-rec-card';
    card.onclick = function(){ switchTab('tmdb'); };
    var fallbackHtml =
      '<div class="ov-rec-poster-fallback"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></div>';
    var posterHtml = src
      ? '<img class="ov-rec-img" src="' + esc(src) + '" alt="" loading="lazy" decoding="async" onerror="this.style.display=\'none\'">' + fallbackHtml
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
  // 3.1 统一 loading 态
  var btn=document.getElementById('runNowBtn');
  if(btn)btn.classList.add('loading');
  try{
    await apiPost('/api/schedule', {action:'run_now'});
    showToast('已触发定时任务', true);
    loadOverviewPage();
  }catch(e){
    showToast('触发失败: ' + (e.message || '未知错误'), false);
  }finally{
    if(btn)btn.classList.remove('loading');
  }
}
