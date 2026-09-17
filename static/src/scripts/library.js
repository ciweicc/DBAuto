// ============ 媒体库（双向管理，issue #27） ============
// 目标：把「夸克网盘真实文件」与「QAS 转存任务」显式展示出来，并允许在网盘侧删除后
// 联动清理 DBAuto 的转存历史——否则网盘删了、本地仍判为「已转存」，重新转存会被跳过。
//
// 后端接口：
//   GET  /api/qas/tasks          QAS tasklist
//   GET  /api/qas/library?path=  网盘目录
//   POST /api/qas/delete         {entries:[{fid,name,title?}], sync_history:true}
//   POST /api/qas/delete_tasks   {tasks:[{taskname,savepath}]} 删除 QAS 任务配置
//                                （QAS 为自部署服务，改动只影响自己的任务表）
//
// ---- 为什么不显示图片（issue #27 反馈「有一些有图，有一些没图，太丑了」） ----
// 上一版卡片会拿文件名去 TMDB 匹配海报。问题是网盘目录名/文件名与 TMDB 的命中率
// 天然不可能全中：命中就贴着海报、未命中就是一大块灰色占位块，同一行里两种形态混排，
// 越大的目录越花。用户明确要求「干脆直接不要图了」，因此本轮：
//   1. 卡片不再有缩略图/海报/评分角标，改为「图标 + 名称 + 元信息」的等高紧凑卡；
//   2. 前端不再调用 /api/tmdb/match（后端接口与匹配实现保留，未删除）；
//   3. 「图文视图 / 列表视图」改名为「卡片视图 / 列表视图」—— 已经没有图了。
//
// ---- 加载慢的根因与优化（issue #27 反馈「网盘文件模块访问有点慢」） ----
// 1. 根因一：单次目录请求的失败预算被拉到 33s。
//    后端 /api/qas/library 走 QASClient(timeout=15)，底层 http_get 对网络层错误
//    会重试 3 次。QAS 不可达/超时时用户实际等待最坏 ≈ 15+1+5+5+5+1 = 32s，
//    期间页面只有「加载中...」，观感就是「卡死」。
//    → 本页目录请求改为**短预算 + 单次轻量重试 + 8s 软超时**（见 libFetch），
//      失败立刻给出可重试的错误态，而不是让用户盯着转圈。
// 2. 根因二：首屏并行发 2 个请求，且「刷新」没有任何本地复用。
//    → 目录请求与任务请求**并行**发起，互不阻塞（先渲染先到的）；
//      同一目录按 TTL 复用最近一次响应（翻进子目录再返回不再打网络）。
// 3. 根因三：缺少进度反馈，无法区分「慢」与「死了」。
//    → 显式 loading 状态 + 时间轴提示（「已用 3.2s，正在等 QAS 响应…」）。
// 4. 渲染保持增量不变：加载中/错误/空态用静态 HTML 一次性赋值，不走多次重排。

// 媒体库根目录（issue #27）：把「资源根目录」设置项当作浏览下界。
// 默认 '/'（网盘根）；用户可在设置里指定 /影视、/媒体库 等，媒体库页即以此为起点。
function libRoot(){
  var r = (typeof APP_PATHS !== 'undefined' && APP_PATHS && APP_PATHS.library_root) || '/';
  r = String(r).trim();
  if(!r || r.charAt(0) !== '/') return '/';
  return r.replace(/\/+$/,'') || '/';
}
function libAtRoot(path){
  var p = String(path == null ? '' : path).replace(/\/+$/,'') || '/';
  return p === libRoot();
}
var libPath = libRoot();
var libEntries = [];
var libSelected = {};
var qasTasks = [];
var qasSelected = {};      // QAS 任务的勾选状态：key -> task

// 目录响应缓存：短时间内重复进入同一目录直接复用，避免无谓往返。
// 只缓存「成功」结果；失败不缓存，用户重试才会真的重试。
var LIB_CACHE_TTL = 60000;
var libCache = {};        // path -> {ts, entries}
var libInflight = {};     // path -> Promise（同一目录并发只打一次）

// 两种视图（issue #27）：卡片视图（默认，等高的紧凑卡）/ 列表视图（紧凑表格）。
// 偏好存 localStorage，用户切过一次之后一直记住。
var LIB_VIEW_KEY = 'lib_view';       // 'card' | 'list'
var libView = 'card';

function libViewMode(){
  try{
    var v = localStorage.getItem(LIB_VIEW_KEY);
    if(v === 'list' || v === 'card') return v;
  }catch(e){}
  return 'card';
}

function toggleLibView(){
  libView = (libView === 'card') ? 'list' : 'card';
  try{ localStorage.setItem(LIB_VIEW_KEY, libView); }catch(e){}
  _updateLibViewBtn();
  _renderLibrary(libPath, libEntries, false, null, true);
}

function _updateLibViewBtn(){
  var btn = document.getElementById('libViewBtn');
  if(!btn) return;
  btn.textContent = (libView === 'card') ? '列表视图' : '卡片视图';
  btn.setAttribute('aria-label', (libView === 'card') ? '切换为列表视图' : '切换为卡片视图');
}

// 单请求预算：与 QAS「本地服务」定位匹配，宁可快速失败也不让用户干等 30s+
//
// 注意：这里是**整次调用**的墙钟预算，而不是「每次尝试各 8s」。
// 踩坑记录（issue #27）：最初写成「每次尝试各自 setTimeout(8s) + 重试 1 次」，
// 实测最坏等待变成 8s + 退避 + 8s ≈ 16.4s —— 用户仍然在干等，
// 「快速失败」形同虚设。现在超时定时器只在进入循环时起一次，
// 到点即中止：无论重试到第几次，总等待都不会超过 LIB_REQ_TIMEOUT。
var LIB_REQ_TIMEOUT = 8000;   // 整次调用的软超时（AbortController）
var LIB_RETRIES = 1;          // 仅 1 次重试，失败立刻报错
var LIB_RETRY_DELAY = 400;    // 重试前的轻量退避

function fmtSize(n){
  if(!n || n <= 0) return '—';
  var u = ['B','KB','MB','GB','TB'], i = 0;
  while(n >= 1024 && i < u.length - 1){ n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}

function fmtTs(ts){
  if(!ts) return '—';
  var d = new Date(ts * 1000);
  if(isNaN(d.getTime())) return '—';
  var p = function(x){ return (x < 10 ? '0' : '') + x; };
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

// 紧凑时间（月-日 时:分）：卡片很窄时用的降级格式，避免时间被省略号吃掉。
// 完整时间（含年份）始终在 title 与移动端布局里可见，见 _libMetaHtml。
function fmtTsShort(ts){
  var full = fmtTs(ts);
  if(full === '—') return full;
  var m = /^\d{4}-(\d{2}-\d{2} \d{2}:\d{2})$/.exec(full);
  return m ? m[1] : full;
}

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

// 把字符串安全地嵌进内联事件处理器的参数位（属性用双引号包裹）。
//
// 踩坑记录（issue #27）：直接用 JSON.stringify 会产出 "f2"（带双引号），
// 与 onchange="..." 的双引号冲突，HTML 解析后属性被截断：
//   onchange="toggleLibSelect(" f2",="" this.checked)"
// 于是勾选毫无反应。这里改为产出**单引号**字符串字面量，
// 并转义反斜杠、单引号与 < & " ，避免破坏属性与注入。
function jsArg(v){
  var t = String(v == null ? '' : v)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/</g, '\\u003c')
    .replace(/&/g, '\\u0026')
    .replace(/"/g, '\\u0022');
  return "'" + t + "'";
}

function loadLibraryPage(){
  // 目录与任务互不依赖：并行发起，先到先渲染（原先串行 await 白等一倍时间）
  libView = libViewMode();
  _updateLibViewBtn();
  // 首次进入且尚未导航过时，落到设置里的「媒体库根目录」。
  // （libPath 在脚本加载期就已初始化为当时的 libRoot，此处覆盖为最新配置）
  loadLibrary(libPath);
  loadQasTasks();
  _syncLibRootFromSettings();
}

// 配置是异步拉取的：首次进入库页时把起点对齐到最新配置的根目录。
// 仅当「当前仍在初始起点（尚未导航）」时重定位，避免打断用户已翻进去的目录。
var _libRootSynced = false;
async function _syncLibRootFromSettings(){
  if(_libRootSynced) return;
  _libRootSynced = true;
  var initial = libPath;
  try{
    // 复用 library.js 的短预算通道（本文件禁用 apiGet，避免叠加长重试等待）
    var r = await libFetch('/api/settings/all');
    var all = (r && r.ok && r.data) ? r.data : null;
    if(all) SETTINGS_ALL = all;
    var sp = (all && all.schedule && all.schedule.savepaths) || {};
    if(!sp.library_root) return;
    APP_PATHS.library_root = sp.library_root;
    var root = libRoot();
    if(root !== initial && libPath === initial){
      loadLibrary(root, {force:true});   // 用户没动过，直接对齐到配置根目录
    }
  }catch(e){}
}

// ---------- 短预算请求（见文件头「加载慢的根因」） ----------
// 返回 {ok:true, data, ms} / {ok:false, message, ms}，不抛异常。
// 超时预算覆盖整个重试过程（见 LIB_REQ_TIMEOUT 的踩坑说明）。
async function libFetch(url){
  var t0 = Date.now();
  var lastErr = null;
  var ctrl = null;
  if(typeof AbortController === 'function'){
    ctrl = new AbortController();
    setTimeout(function(){ try{ ctrl.abort(); }catch(e){} }, LIB_REQ_TIMEOUT);
  }
  for(var i = 0; i <= LIB_RETRIES; i++){
    if(ctrl && ctrl.signal && ctrl.signal.aborted){
      lastErr = new Error('请求超时（' + (LIB_REQ_TIMEOUT/1000) + 's）');
      break;
    }
    try{
      var r = await fetch(url, { headers: authHeaders(), signal: ctrl ? ctrl.signal : undefined });
      if(r.status === 401){
        TOKEN = ''; localStorage.removeItem('auth_token');
        showToast('登录已过期，正在跳转...', false);
        setTimeout(function(){ location.href = '/login.html'; }, 800);
        return { ok:false, message:'登录已过期', ms: Date.now() - t0 };
      }
      if(!r.ok){
        lastErr = new Error('HTTP ' + r.status);
      }else{
        return { ok:true, data: await r.json(), ms: Date.now() - t0 };
      }
    }catch(e){
      // 软超时 / 网络异常：都走「快速失败」路径，不再叠加后端级长重试
      lastErr = (e && e.name === 'AbortError')
        ? new Error('请求超时（' + (LIB_REQ_TIMEOUT/1000) + 's）')
        : e;
    }
    if(i < LIB_RETRIES && !(ctrl && ctrl.signal && ctrl.signal.aborted)){
      await new Promise(function(res){ setTimeout(res, LIB_RETRY_DELAY); });
    }
  }
  return { ok:false, message: (lastErr && lastErr.message) || '请求失败', ms: Date.now() - t0 };
}

// ---------- QAS 任务 ----------

async function loadQasTasks(){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  qasSelected = {};
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">加载中...</div></div>';
  var r = await libFetch('/api/qas/tasks');
  if(!r.ok){
    _renderQasTasksError(r.message);
    return;
  }
  var res = r.data || {};
  if(!res.success){
    _renderQasTasksError(res.message || '请检查 QAS 配置');
    return;
  }
  qasTasks = res.tasks || [];
  _renderQasTasks();
}

// QAS 任务的定位键：QAS 不保证 taskname 唯一，故用 taskname + savepath。
// 刷新后若任务被改名/移动，键会自然失配（勾选清空），不会误删别的任务。
function qasKey(t){
  return String(t.taskname == null ? '' : t.taskname) + '\u0000' + String(t.savepath == null ? '' : t.savepath);
}

function _renderQasTasksError(message){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  var cnt = document.getElementById('qasTaskCount');
  if(cnt) cnt.textContent = '';
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px">' +
    '<div class="empty-title">读取失败</div>' +
    '<div class="empty-desc">' + esc(message || '请检查 QAS 配置') + '</div>' +
    '<div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadQasTasks()">重试</button></div>' +
    '</div>';
  updateQasActionBar();
}

function _renderQasTasks(){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  var cnt = document.getElementById('qasTaskCount');
  if(cnt) cnt.textContent = qasTasks.length ? '共 ' + qasTasks.length + ' 条' : '';
  if(!qasTasks.length){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">暂无任务</div></div>';
    updateQasActionBar();
    return;
  }
  var html = '<div class="lib-table lib-table-tasks">';
  html += '<div class="lib-row lib-head"><span class="lib-cb"></span><span>任务名</span><span>转存路径</span><span>分享链接</span><span></span></div>';
  qasTasks.forEach(function(t){
    var key = qasKey(t);
    // 行内只带 key（不把任务名拼进 onclick 字符串），避免引号/特殊字符破坏属性
    html += '<div class="lib-row">' +
      '<span class="lib-cb"><input type="checkbox" aria-label="选择 ' + esc(t.taskname) + '" onchange="toggleQasSelect(' + jsArg(key) + ', this.checked)"></span>' +
      '<span class="lib-name" title="' + esc(t.taskname) + '">' + esc(t.taskname || '—') + '</span>' +
      '<span class="lib-sub" title="' + esc(t.savepath) + '">' + esc(t.savepath || '—') + '</span>' +
      '<span class="lib-sub">' + (t.shareurl ? '<a href="' + esc(t.shareurl) + '" target="_blank" rel="noopener noreferrer">链接</a>' : '—') + '</span>' +
      '<span class="lib-ops"><button class="btn btn-sm btn-outline" onclick="deleteQasTask(' + jsArg(key) + ')">删除</button></span>' +
      '</div>';
  });
  html += '</div>';
  box.innerHTML = html;
  updateQasActionBar();
}

function findQasTask(key){
  for(var i = 0; i < qasTasks.length; i++){
    if(qasKey(qasTasks[i]) === key) return qasTasks[i];
  }
  return null;
}

function toggleQasSelect(key, on){
  var t = findQasTask(key);
  if(!t) return;
  if(on) qasSelected[key] = t; else delete qasSelected[key];
  updateQasActionBar();
}

function updateQasActionBar(){
  var bar = document.getElementById('qasActionBar');
  var n = Object.keys(qasSelected).length;
  if(bar) bar.style.display = qasTasks.length ? 'flex' : 'none';
  var cnt = document.getElementById('qasSelCount');
  if(cnt) cnt.textContent = n ? '已选 ' + n + ' 条' : '';
  var btn = document.getElementById('qasDelBtn');
  if(btn) btn.disabled = n === 0;
  var all = document.getElementById('qasSelAll');
  if(all){
    all.checked = n > 0 && n === qasTasks.length;
    all.indeterminate = n > 0 && n < qasTasks.length;
  }
}

function toggleQasSelectAll(on){
  qasSelected = {};
  if(on){
    qasTasks.forEach(function(t){ qasSelected[qasKey(t)] = t; });
  }
  // 重渲染以同步表头的全选态与各行的勾选框
  _renderQasTasks();
}

// ---------- 删除 QAS 任务（issue #27 跟进） ----------

// 删除 QAS 的条目任务配置。用户确认 QAS 是自部署服务，因此这里的改动只影响
// 自己的任务表，不存在「改别人的任务配置」的风险。
//
// 两个刻意的设计：
//  1. 删除前必须确认，且文案里写清「任务配置会被移除、网盘里已转存的文件不受影响」，
//     避免用户以为连文件一起删了；
//  2. 后端是**整表写回**（QAS /update 为整表替换语义），失败时后端不做任何改动。
async function doDeleteQasTasks(items, label){
  if(!items || !items.length) return;
  // 同 doDelete：确认框返回 false 时绝不能继续（issue #27）
  var confirmed = await confirmThen('删除 QAS 任务', '将从夸克自动转存中移除 ' + label + '。\n' +
    '只删除任务配置（分享链接/保存路径/匹配规则），网盘里已转存的文件不受影响。',
    '删除', '取消');
  if(!confirmed) return;

  var btn = document.getElementById('qasDelBtn');
  if(btn) btn.classList.add('loading');
  try{
    var res = await apiPost('/api/qas/delete_tasks', { tasks: items });
    if(res.success){
      showToast('已删除 ' + res.deleted + ' 条 QAS 任务', true);
      // 后端返回写回后的完整列表，直接复用可省掉一次往返
      if(res.tasks){
        qasTasks = res.tasks;
        qasSelected = {};
        _renderQasTasks();
        return;
      }
    }else{
      showToast(res.message || '删除失败，QAS 未做改动', false);
      return;
    }
  }catch(e){
    showToast('删除失败: ' + e.message, false);
    return;
  }finally{
    if(btn) btn.classList.remove('loading');
  }
  loadQasTasks();
}

function deleteQasTask(key){
  var t = findQasTask(key);
  if(!t) return;
  doDeleteQasTasks([{ taskname: t.taskname, savepath: t.savepath }], '任务「' + t.taskname + '」');
}

function deleteSelectedQasTasks(){
  var keys = Object.keys(qasSelected);
  if(!keys.length) return;
  var items = keys.map(function(k){
    var t = qasSelected[k];
    return { taskname: t.taskname, savepath: t.savepath };
  });
  doDeleteQasTasks(items, '所选的 ' + items.length + ' 条任务');
}

// ---------- 网盘文件 ----------

// 加载态文案随时间推进：让用户能区分「在等」和「卡死」。
// 由调用方传入 t0，避免额外状态泄漏到全局。
function _libLoadingHtml(t0){
  var s = ((Date.now() - t0) / 1000).toFixed(1);
  return '<div class="empty-state" style="padding:24px 20px">' +
    '<div class="empty-title">加载中...</div>' +
    '<div class="empty-desc" id="libLoadingHint">已用 ' + s + 's，正在等 QAS 响应…（超过 ' + (LIB_REQ_TIMEOUT/1000) + 's 会自动失败可重试）</div>' +
    '</div>';
}

async function loadLibrary(path, opts){
  opts = opts || {};
  var box = document.getElementById('libList');
  if(!box) return;
  var target = path || '/';
  var force = !!opts.force;
  var t0 = Date.now();

  // 1. 命中缓存：先渲染旧数据（瞬间出内容），再按需后台刷新
  var hit = libCache[target];
  var fresh = hit && !force && (Date.now() - hit.ts) < LIB_CACHE_TTL;
  if(fresh){
    _renderLibrary(target, hit.entries, true);
    return;
  }
  if(hit && !force){
    _renderLibrary(target, hit.entries, true);   // 陈旧数据先顶上，避免白屏
  }else{
    box.innerHTML = _libLoadingHtml(t0);
  }
  var hintTimer = setInterval(function(){
    var el = document.getElementById('libLoadingHint');
    if(!el){ clearInterval(hintTimer); return; }
    el.textContent = '已用 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's，正在等 QAS 响应…（超过 ' + (LIB_REQ_TIMEOUT/1000) + 's 会自动失败可重试）';
  }, 1000);

  try{
    // 2. 同一目录并发请求合并为一次（例如连点「刷新」）
    var key = target;
    if(!libInflight[key]){
      libInflight[key] = libFetch('/api/qas/library?path=' + encodeURIComponent(target))
        .then(function(r){ delete libInflight[key]; return r; });
    }
    var r = await libInflight[key];
    clearInterval(hintTimer);

    if(!r.ok){
      if(!hit) _renderLibError(target, r.message, r.ms);
      else showToast('刷新失败：' + r.message + '（显示的是缓存数据）', false);
      return;
    }
    var res = r.data || {};
    if(!res.success){
      if(!hit) _renderLibError(target, res.message || '请检查 QAS 配置', r.ms);
      else showToast('刷新失败：' + (res.message || 'QAS 返回异常') + '（显示的是缓存数据）', false);
      return;
    }
    var entries = res.entries || [];
    libCache[target] = { ts: Date.now(), entries: entries };
    _renderLibrary(target, entries, false, r.ms);
  }catch(e){
    clearInterval(hintTimer);
    if(!hit) _renderLibError(target, e.message, Date.now() - t0);
  }
}

function _renderLibError(path, message, ms){
  var box = document.getElementById('libList');
  if(!box) return;
  libPath = path; libEntries = []; libSelected = {};
  renderBreadcrumb(path);
  var cnt = document.getElementById('libCount');
  if(cnt) cnt.textContent = '';
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px">' +
    '<div class="empty-title">读取失败</div>' +
    '<div class="empty-desc">' + esc(message || '请检查 QAS 配置') + '</div>' +
    '<div class="empty-desc">已用 ' + (ms/1000).toFixed(1) + 's。网盘目录由 QAS 实时向夸克拉取，' +
    '若持续超时请检查 QAS 是否在线、夸克 Cookie 是否失效。</div>' +
    '<div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadLibrary(libPath, {force:true})">重试</button></div>' +
    '</div>';
  updateLibActionBar();
}

// 渲染一次目录（缓存命中与网络返回走同一条路径，避免两份 DOM 逻辑漂移）
function _renderLibrary(path, entries, fromCache, ms){
  var box = document.getElementById('libList');
  if(!box) return;
  libPath = path;
  libEntries = entries || [];
  libSelected = {};
  libEntries.forEach(function(it, i){
    it._key = it.fid != null ? String(it.fid) : ('p' + i);
  });
  renderBreadcrumb(libPath);
  var upBtn = document.getElementById('libUpBtn');
  if(upBtn) upBtn.disabled = libAtRoot(libPath);
  var cnt = document.getElementById('libCount');
  if(cnt){
    var parts = [];
    if(libEntries.length) parts.push('共 ' + libEntries.length + ' 项');
    if(typeof ms === 'number' && ms >= 0) parts.push('耗时 ' + (ms/1000).toFixed(1) + 's');
    if(fromCache) parts.push('缓存');
    cnt.textContent = parts.join(' · ');
  }
  if(!libEntries.length){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">空目录</div></div>';
    updateLibActionBar();
    return;
  }
  libEntries.sort(function(a,b){
    if(a.dir !== b.dir) return a.dir ? -1 : 1;
    return String(a.name).localeCompare(String(b.name), 'zh');
  });
  libView = libView || libViewMode();
  _updateLibViewBtn();
  box.innerHTML = (libView === 'card') ? _libCardHtml() : _libTableHtml();
  updateLibActionBar();
}

// ---------- 列表视图（保留原有紧凑表格，随时可切回） ----------
function _libTableHtml(){
  var html = '<div class="lib-table">';
  // 列表视图的表头与卡片视图保持同一套语义：名称 / 大小 / 修改时间（issue #27 跟进）。
  html += '<div class="lib-row lib-head"><span class="lib-cb"></span><span>名称</span><span>大小</span><span>修改时间</span><span></span></div>';
  libEntries.forEach(function(it){
    html += '<div class="lib-row">' +
      '<span class="lib-cb"><input type="checkbox" aria-label="选择 ' + esc(it.name) + '" onchange="toggleLibSelect(' + jsArg(it._key) + ', this.checked)"></span>' +
      '<span class="lib-name">' +
        (it.dir
          ? '<a href="javascript:void(0)" onclick="enterDir(' + jsArg(it.name) + ')"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline;vertical-align:-3px;margin-right:4px"><use href="#icon-folder"/></svg>' + esc(it.name) + '</a>'
          : esc(it.name)) +
      '</span>' +
      '<span class="lib-sub">' + (it.dir ? '—' : fmtSize(it.size)) + '</span>' +
      // 修改时间：单独一格 + title，便于扫视/复制（与卡片视图同一格式）
      '<span class="lib-sub lib-sub-time" title="修改时间 ' + esc(fmtTs(it.updated_at)) + '">' + fmtTs(it.updated_at) + '</span>' +
      '<span class="lib-ops"><button class="btn btn-sm btn-outline" onclick="deleteOne(' + jsArg(it._key) + ')">删除</button></span>' +
      '</div>';
  });
  html += '</div>';
  return html;
}

// ---------- 卡片视图（等高紧凑卡，issue #27 观感重做） ----------
//
// 排版取舍（issue #27 两轮反馈的真因）：
//   1. 第一版：整张卡做成 2:3 海报框 → 有海报的卡很高、无海报的卡一大块纯色空框，
//      行内高矮参差、大片空白；
//   2. 第二版：缩略块改成固定 16:9 想压平高度，但「有些卡有图、有些卡没图」本身
//      就是观感噪音 —— 灰块与海报混排，越大的目录越花；
//   3. 现在（本轮）：**整块缩略图去掉**。卡片 = 「一行图标 + 名称 + 元信息」，
//      文件夹用文件夹图标、文件用影片图标，所有卡天然等高、无空框、无占位块。
//
// 卡片内所有元素都在底部信息行里：
//   [勾选框] [图标 + 名称 / 元信息] [删除图标]
// 「进入目录」仍然是文件夹卡标题上的链接（图标与标题同在链接里）。
function _libCardHtml(){
  // 条目多时给网格一个高度上限（用户可以在卡片内滚动，不用被一堆卡推着翻页面）。
  // 记录数量用于走查断言：行数多而高度未被限制 = 会变成「一屏全是卡」。
  var box = document.getElementById('libList');
  if(box) box.setAttribute('data-lib-count', String(libEntries.length));
  var html = '<div class="lib-grid" id="libGrid">';
  libEntries.forEach(function(it){
    html += it.dir ? _libDirCardHtml(it) : _libFileCardHtml(it);
  });
  html += '</div>';
  return html;
}

// 卡片外壳：勾选框 / 删除按钮 / 标题分别渲染。
// 图标（文件夹 / 影片）由调用方通过 icon 参数给出 SVG use id。
function _libCardShell(it, kind, icon){
  var nameHtml = it.dir
    ? '<a class="lib-card-title lib-card-dirlink" href="javascript:void(0)" onclick="enterDir(' + jsArg(it.name) + ')" title="进入 ' + esc(it.name) + '">' + esc(it.name) + '</a>'
    : '<span class="lib-card-title" title="' + esc(it.name) + '">' + esc(it.name) + '</span>';
  return '<div class="lib-card lib-card-' + kind + '" data-key="' + esc(it._key) + '" data-name="' + esc(it.name) + '">' +
    '<label class="lib-card-cb" title="选择该' + (it.dir ? '文件夹' : '文件') + '">' +
      '<input type="checkbox" aria-label="选择 ' + esc(it.name) + '" onchange="toggleLibSelect(' + jsArg(it._key) + ', this.checked)">' +
    '</label>' +
    '<span class="lib-card-icon" aria-hidden="true">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><use href="#' + icon + '"/></svg>' +
    '</span>' +
    '<span class="lib-card-name-col">' +
      nameHtml +
      _libMetaHtml(it) +
    '</span>' +
    '<button class="lib-icon-btn" title="删除" aria-label="删除 ' + esc(it.name) + '" onclick="deleteOne(' + jsArg(it._key) + ')">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><use href="#icon-trash"/></svg>' +
    '</button>' +
  '</div>';
}

// 元信息（issue #27 跟进：用户要求「添加显示修改时间」）。
//
// 有图的那版里「大小 · 修改时间」被挤在同一行；去掉图以后卡片虽变宽，
// 但两段都是数字、扫视时区分不出哪一段是时间。现在拆成**两行**：
//   - 第一行：文件大小（文件夹则显示「文件夹」）；
//   - 第二行：修改时间，带时钟图标 + 「修改」前缀。
//
// 踩坑（本轮实测）：卡片只有 180~240px 宽，`修改 2026-09-17 20:04`（约 112px）
// 加上图标与留白后**装不下**，实测 1440 与 2560 视口下时间仍被省略号截成
// `修改 2025-0…` —— 用户看到的还是「没有修改时间」。因此时间行做两层：
//   - `.lib-time-full`（yyyy-mm-dd hh:mm，移动端 1 列时够宽，可完整显示）；
//   - `.lib-time-short`（mm-dd hh:mm，紧凑卡下显示）。
// 两者由 CSS 容器查询按**实际可用宽度**取舍（@container timefit，见 main.css），
// 容器查询不支持时默认显示完整格式；完整值同时放在 title 里，任何情况下都能看到。
function _libMetaHtml(it){
  if(it.dir) return '<span class="lib-card-meta">文件夹</span>';
  var full = fmtTs(it.updated_at);
  var short = fmtTsShort(it.updated_at);
  return '<span class="lib-card-meta">' + esc(fmtSize(it.size)) + '</span>' +
    '<span class="lib-card-time" title="修改时间 ' + esc(full) + '">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#icon-clock"/></svg>' +
      '<span class="lib-time-text lib-time-full">修改 ' + esc(full) + '</span>' +
      '<span class="lib-time-text lib-time-short">修改 ' + esc(short) + '</span>' +
    '</span>';
}

// 单行拼接口径（「大小 · 修改时间」）：列表视图与提示文案复用，保证全站时间格式一致。
function _libMetaText(it){
  if(it.dir) return '文件夹';
  var bits = [fmtSize(it.size)];
  var ts = fmtTs(it.updated_at);
  if(ts && ts !== '—') bits.push(ts);
  return bits.join(' · ');
}

function _libDirCardHtml(it){
  return _libCardShell(it, 'dir', 'icon-folder');
}

function _libFileCardHtml(it){
  return _libCardShell(it, 'file', 'icon-movie');
}

function invalidateLibCache(path){
  delete libCache[path == null ? libPath : path];
}

// 刷新按钮：强制绕过缓存
function refreshLibrary(){
  invalidateLibCache(libPath);
  loadLibrary(libPath, {force:true});
}

function renderBreadcrumb(path){
  var el = document.getElementById('libBreadcrumb');
  if(!el) return;
  var root = libRoot();
  var p = String(path || '/');
  // 相对根目录的片段：根目录自身不显示前缀段
  var rel = p;
  if(root !== '/' && (p === root || p.indexOf(root + '/') === 0)){
    rel = p.slice(root.length);
  }else if(root === '/'){
    rel = p;
  }else{
    rel = p;   // 越界路径（理论上被 libGoUp/libEnterDir 拦下）仍按原文显示
  }
  var parts = rel.split('/').filter(Boolean);
  var rootLabel = root === '/' ? '根目录' : esc(root);
  var html = '<a href="javascript:void(0)" onclick="loadLibrary(' + jsArg(root) + ')">' + rootLabel + '</a>';
  var acc = root === '/' ? '' : root;
  parts.forEach(function(seg){
    acc += '/' + seg;
    html += ' <span class="lib-sep">/</span> <a href="javascript:void(0)" onclick="loadLibrary(' + jsArg(acc) + ')">' + esc(seg) + '</a>';
  });
  el.innerHTML = html;
}

function enterDir(name){
  var base = libPath === '/' ? '' : libPath;
  loadLibrary(base + '/' + name);
}

function libGoUp(){
  if(libAtRoot(libPath)) return;   // 媒体库根目录即下界，不允许再往上（issue #27）
  var parts = libPath.split('/').filter(Boolean);
  parts.pop();
  var next = '/' + parts.join('/');
  if(next === '') next = '/';
  // 若上级已越过配置根目录，则停在根目录
  var root = libRoot();
  if(root !== '/' && next !== root && next.indexOf(root + '/') !== 0) next = root;
  loadLibrary(next);
}

function findEntry(key){
  for(var i = 0; i < libEntries.length; i++){
    if(libEntries[i]._key === key) return libEntries[i];
  }
  return null;
}

function toggleLibSelect(key, on){
  var it = findEntry(key);
  if(!it) return;
  if(on) libSelected[key] = it; else delete libSelected[key];
  updateLibActionBar();
}

function updateLibActionBar(){
  var bar = document.getElementById('libActionBar');
  var n = Object.keys(libSelected).length;
  if(bar) bar.style.display = libEntries.length ? 'flex' : 'none';
  var allBar = document.getElementById('libSelectAllBar');
  if(allBar) allBar.style.display = libEntries.length ? 'flex' : 'none';
  var cnt = document.getElementById('libSelCount');
  if(cnt) cnt.textContent = n ? '已选 ' + n + ' 项' : '';
  var btn = document.getElementById('libDelBtn');
  if(btn) btn.disabled = n === 0;
  var all = document.getElementById('libSelAll');
  if(all){
    all.checked = n > 0 && n === libEntries.length;
    all.indeterminate = n > 0 && n < libEntries.length;
  }
}

function toggleLibSelectAll(on){
  libSelected = {};
  if(on){
    libEntries.forEach(function(it){ libSelected[it._key] = it; });
  }
  // 就地同步每行的勾选框，避免整表重渲染把当前选择清空
  libEntries.forEach(function(it){
    var cb = document.querySelector('input[aria-label="选择 ' + _cssQuote(it.name) + '"]');
    if(cb) cb.checked = !!libSelected[it._key];
  });
  updateLibActionBar();
}

// 供 querySelector 拼接属性选择器用：转义引号与反斜杠
function _cssQuote(v){
  return String(v == null ? '' : v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// ---------- 删除（含历史同步） ----------

// 文件名通常带后缀/清晰度（如 电影A.2024.2160p.mkv），历史里存的是作品名。
// 这里先剥掉扩展名再去匹配；匹配不到时后端会如实回报「历史无匹配」，不会误删。
function guessTitle(name){
  if(!name) return '';
  return String(name).replace(/\.[a-zA-Z0-9]{2,5}$/, '').trim();
}

async function doDelete(entries, label){
  if(!entries.length) return;
  var sync = document.getElementById('syncHistoryChk');
  var syncHistory = !sync || sync.checked;
  var extra = syncHistory ? '\n同时会清理 DBAuto 中对应的转存历史（匹配不到则跳过）。' : '';
  // 必须检查返回值：取消/遮罩/ESC 都是 false，此时不能删除（issue #27 严重问题）
  var confirmed = await confirmThen('确认删除', '将从夸克网盘删除 ' + label + '。' + extra, '删除', '取消');
  if(!confirmed) return;

  var btn = document.getElementById('libDelBtn');
  if(btn) btn.classList.add('loading');
  try{
    var res = await apiPost('/api/qas/delete', {
      entries: entries,
      sync_history: syncHistory
    });
    if(res.success){
      showToast('已删除 ' + res.deleted + ' 项' + (res.history_synced ? '，同步清理历史 ' + res.history_synced + ' 条' : ''), true);
    }else if(res.deleted > 0){
      showToast('部分成功：删除 ' + res.deleted + '，失败 ' + res.failed, false);
    }else{
      showToast(res.message || '删除失败', false);
    }
  }catch(e){
    showToast('删除失败: ' + e.message, false);
  }finally{
    if(btn) btn.classList.remove('loading');
  }
  // 删除后列表必然变化，缓存作废再拉取
  invalidateLibCache(libPath);
  loadLibrary(libPath, {force:true});
}

function deleteOne(key){
  var it = findEntry(key);
  if(!it) return;
  doDelete([{fid: it.fid, name: it.name, title: guessTitle(it.name)}], it.name);
}

function deleteSelected(){
  var keys = Object.keys(libSelected);
  if(!keys.length) return;
  var entries = keys.map(function(k){
    var it = libSelected[k];
    return {fid: it.fid, name: it.name, title: guessTitle(it.name)};
  });
  doDelete(entries, '所选的 ' + entries.length + ' 项');
}
