// ============ Init ============
function initTimeSelects(){
  var hours = ['schedTHour', 'schedEHour'];
  var mins = ['schedTMin', 'schedEMin'];
  for(var h = 0; h < hours.length; h++){
    var sel = document.getElementById(hours[h]);
    if(sel) for(var i = 0; i < 24; i++){ var o = document.createElement('option'); o.value = i; o.textContent = String(i).padStart(2, '0'); sel.appendChild(o); }
  }
  for(var m = 0; m < mins.length; m++){
    var sel2 = document.getElementById(mins[m]);
    if(sel2) for(var i = 0; i < 60; i += 5){ var o2 = document.createElement('option'); o2.value = i; o2.textContent = String(i).padStart(2, '0'); sel2.appendChild(o2); }
  }
}

async function init(){
  initTheme(); initDensity(); initTimeSelects(); updateSoundBtn(); initShortcuts();
  // 概览页栅格由「主列实测宽度」驱动（见 overview.js / docs/UI_Roadmap_Next.md 第十节）
  initOverviewViewport();
  try{ C = await apiGet('/api/categories'); }
  catch(e){ showToast('认证失败，请重新登录', false); setTimeout(function(){ location.href = '/login.html'; }, 1500); return; }
  parseCategories(); parseSchedCats(); loadSchedule(); loadExecHistory();
  checkRunningStatus(); loadDashboard();
  switchTab(currentTab);
  // 发现区常驻概览页：首屏就绪后按需初始化（渲染海报墙与筛选选项）。
  // 不放在 switchTab 里是因为 switchTab 可能先于其它模块的 DOM 就绪被调用。
  if(currentTab === 'overview'){ try{ initTmdbPage(); }catch(e){ console.error('TMDB init error', e); } }
  initSSE();
}

// ---- 抽屉 / 日志面板开合（移动端） ----
function toggleSidebar(){
  var s = document.querySelector('.sidebar');
  if(!s) return;
  toggleScrim(s.classList.toggle('open'));
}
function toggleLogDrawer(){
  var p = document.getElementById('logPanel');
  if(!p) return;
  // 桌面态：折叠态由 .collapsed 控制（网格轨道内常驻），FAB 应展开而非切换抽屉，
  // 否则折叠后既无展开入口、FAB 又不起作用（曾导致日志栏"消失"）。
  if(!isNarrowViewport()){
    if(p.classList.contains('collapsed')){ expandLogPanel(); toggleScrim(false); return; }
  }
  // 窄屏抽屉：只有 .open 能让日志栏离开 translateX(100%) 的屏幕外位置。
  // issue #11 复盘：这里此前只 toggle('open')，但若面板同时带着 .collapsed
  // （例如从宽屏折叠态缩窄窗口），.collapsed 的 !important 宽度会把抽屉压回
  // 48px 轨道 —— 点了像没反应。打开时一并清掉折叠态，保证抽屉真正可见。
  var willOpen = !p.classList.contains('open');
  if(willOpen){
    p.classList.add('open');
    if(p.classList.contains('collapsed')) p.classList.remove('collapsed');
    _syncLogPanelA11y();
  }else{
    p.classList.remove('open');
  }
  toggleScrim(willOpen);
  if(willOpen && typeof positionLogDrawerAtLatest === 'function') positionLogDrawerAtLatest();
}
function closeDrawers(){
  var s = document.querySelector('.sidebar'); if(s) s.classList.remove('open');
  var p = document.getElementById('logPanel');
  // 注意：窄屏下日志抽屉必须能被切换页签关掉；但宽屏的「常驻日志栏」
  // 不是抽屉，不能因为切页签就被移除可见性。
  if(p && isNarrowViewport()) p.classList.remove('open');
  var sc = document.getElementById('overlayScrim'); if(sc) sc.classList.remove('show');
}
function toggleScrim(show){
  var sc = document.getElementById('overlayScrim');
  if(!sc) return;
  if(show) sc.classList.add('show'); else sc.classList.remove('show');
}

var sseRetryDelay = 5000;
function initSSE(){
  if(!window.EventSource) return;
  var es = new EventSource('/api/sse');
  es.onopen = function(){
    sseConnected = true;
    sseRetryDelay = 5000;
    if(logPollTimer && logPollInterval === 3000){
      clearInterval(logPollTimer); startLogPoll(10000);
    }
  };
  es.addEventListener('schedule_update', function(){ SETTINGS_ALL = null; loadDashboard(); if(currentTab==='overview') loadOverviewPage(); });
  es.addEventListener('config_update', function(){ SETTINGS_ALL = null; loadConfig(); if(currentTab==='overview') loadOverviewPage(); });
  es.addEventListener('history_update', function(){ loadExecHistory(); if(currentTab==='overview') loadOverviewPage(); });
  es.addEventListener('transfer_progress', function(e){
    var d = JSON.parse(e.data);
    if(d.stats){
      document.getElementById('stSearched').textContent = d.stats.searched || 0;
      document.getElementById('stOK').textContent = d.stats.ok || 0;
      document.getElementById('stSkip').textContent = d.stats.skipped || 0;
      document.getElementById('stFail').textContent = d.stats.failed || 0;
      document.getElementById('stTotal').textContent = d.stats.total || 0;
      updateProgress(d.stats);
    }
    if(d.running){ document.getElementById('stopBtn').style.display = 'inline-block'; expandLogPanel(); }
    else { document.getElementById('stopBtn').style.display = 'none'; if(currentTab==='overview') loadOverviewPage(); }
  });
  es.addEventListener('log', function(e){
    var d = JSON.parse(e.data);
    if(!d.line) return;
    // 后端把「主行 + 详情」一起发：详情挂到这条主行下面折叠（issue #68）。
    // 只有 line 的事件走 addLog（兼容旧后端），它会自己判断要不要折叠。
    if(d.detail) addLogEvent(d.line, d.detail);
    else addLog(d.line);
  });
  es.onerror = function(){
    es.close();
    sseConnected = false;
    if(logPollTimer && logPollInterval === 10000){
      // SSE 断连，切换到 3s 轮询；首次仅对齐游标，不重复添加已有日志
      clearInterval(logPollTimer); startLogPoll(3000, 0, true);
    }
    setTimeout(initSSE, sseRetryDelay);
    sseRetryDelay = Math.min(sseRetryDelay * 2, 30000);
  };
}
// 全局快捷键：Ctrl/⌘+K 聚焦搜索、Ctrl/⌘+Enter 开始转存、Esc 关闭弹窗
//            媒体库页另有选择类快捷键（issue #53），见 _libKeydown
function initShortcuts(){
  // 「最近交互过的列表」标记（issue #53 追问）：两张表并存，按键归谁必须有归属，
  // 见 library.js 的 libKeyScope。委托在 document 上而不是逐行绑 ——
  // 行是重渲染重建的，逐行绑会在每次翻页后丢失。
  ['click', 'focusin', 'mousedown'].forEach(function(evt){
    document.addEventListener(evt, function(e){
      try{ libScopeFromEvent(e); }catch(err){}
    }, true);
  });
  document.addEventListener('keydown', function(e){
    // 媒体库的键盘选择（issue #53）：Ctrl/⌘+A 全选、Shift+↑↓ 范围、↑↓ 移当前行、
    // Space 勾选、Esc 清空选择。
    //
    // 位置很关键：必须排在下面的 Esc 分支**之前**。原来的 Esc 分支对任何
    // Escape 都 `return`（只处理确认框/Sheet，没有就什么都不做），
    // 排在它后面的处理器永远拿不到 Escape —— 实测「Ctrl+A 全选后按 Esc 没反应」
    // 就是这个顺序造成的。而媒体库自己的 Esc 只在「确有选择」时消费该键，
    // 此时不存在同时要关的弹窗（弹窗打开时焦点在遮罩里，列表不会响应）。
    if(_libKeydown(e)) return;
    // QAS 任务表的键盘选择（issue #53 追问：「这里也需要快捷」）：
    // 按键语义与媒体库**逐字一致**，差异只有「作用范围 = 当前可见行」
    // （任务表有筛选 + 分页）。同样必须排在下面的 Esc 分支之前：
    // 那个分支对任何 Escape 都 return，排在它后面就永远收不到 Esc。
    if(_qasKeydown(e)) return;
    // Esc：关闭确认框 / Sheet
    if(e.key === 'Escape'){
      if(document.querySelector('.confirm-overlay.show')){ resolveConfirm(false); e.preventDefault(); return; }
      var sheets = document.querySelectorAll('.sheet-overlay.show');
      if(sheets.length){
        sheets.forEach(function(o){ o.classList.remove('show'); });
        e.preventDefault();
      }
      return;
    }
    // Ctrl/⌘ + K：打开命令面板
    if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k'){
      e.preventDefault();
      openCommandPalette();
      return;
    }
    // Ctrl/⌘ + Enter：开始转存（手动页，且未在编辑设置表单时）
    if((e.ctrlKey || e.metaKey) && e.key === 'Enter'){
      var tag = (e.target && e.target.tagName) ? e.target.tagName : '';
      if(tag === 'TEXTAREA' || tag === 'SELECT') return;
      if(tag === 'INPUT' && e.target.id !== 'searchInput') return;
      if(currentTab === 'manual'){
        e.preventDefault();
        var stopBtn = document.getElementById('stopBtn');
        var running = stopBtn && stopBtn.style.display !== 'none';
        if(!running) startTransfer();
      }
    }
  });
}

init();
