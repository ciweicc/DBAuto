// ============ 媒体库（双向管理，issue #27） ============
// 目标：把「夸克网盘真实文件」与「QAS 转存任务」显式展示出来，并允许在网盘侧删除后
// 联动清理 DBAuto 的转存历史——否则网盘删了、本地仍判为「已转存」，重新转存会被跳过。
//
// 后端接口：
//   GET  /api/qas/tasks          QAS tasklist
//   GET  /api/qas/library?path=  网盘目录
//   POST /api/qas/delete         {entries:[{fid,name,title?}], sync_history:true}
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

var libPath = '/';
var libEntries = [];
var libSelected = {};
var qasTasks = [];

// 目录响应缓存：短时间内重复进入同一目录直接复用，避免无谓往返。
// 只缓存「成功」结果；失败不缓存，用户重试才会真的重试。
var LIB_CACHE_TTL = 60000;
var libCache = {};        // path -> {ts, entries}
var libInflight = {};     // path -> Promise（同一目录并发只打一次）

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
  loadLibrary(libPath);
  loadQasTasks();
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
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">加载中...</div></div>';
  var r = await libFetch('/api/qas/tasks');
  if(!r.ok){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(r.message) + '</div><div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadQasTasks()">重试</button></div></div>';
    var c0 = document.getElementById('qasTaskCount');
    if(c0) c0.textContent = '';
    return;
  }
  var res = r.data || {};
  if(!res.success){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(res.message || '请检查 QAS 配置') + '</div><div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadQasTasks()">重试</button></div></div>';
    document.getElementById('qasTaskCount').textContent = '';
    return;
  }
  qasTasks = res.tasks || [];
  document.getElementById('qasTaskCount').textContent = qasTasks.length ? '共 ' + qasTasks.length + ' 条' : '';
  if(!qasTasks.length){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">暂无任务</div></div>';
    return;
  }
  var html = '<div class="lib-table">';
  html += '<div class="lib-row lib-head"><span>任务名</span><span>转存路径</span><span>分享链接</span></div>';
  qasTasks.forEach(function(t){
    html += '<div class="lib-row">' +
      '<span class="lib-name" title="' + esc(t.taskname) + '">' + esc(t.taskname || '—') + '</span>' +
      '<span class="lib-sub" title="' + esc(t.savepath) + '">' + esc(t.savepath || '—') + '</span>' +
      '<span class="lib-sub">' + (t.shareurl ? '<a href="' + esc(t.shareurl) + '" target="_blank" rel="noopener noreferrer">链接</a>' : '—') + '</span>' +
      '</div>';
  });
  html += '</div>';
  box.innerHTML = html;
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
  if(upBtn) upBtn.disabled = (libPath === '/' || libPath === '');
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
  var html = '<div class="lib-table">';
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
      '<span class="lib-sub">' + fmtTs(it.updated_at) + '</span>' +
      '<span class="lib-ops"><button class="btn btn-sm btn-outline" onclick="deleteOne(' + jsArg(it._key) + ')">删除</button></span>' +
      '</div>';
  });
  html += '</div>';
  box.innerHTML = html;
  updateLibActionBar();
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
  var parts = String(path || '/').split('/').filter(Boolean);
  var html = '<a href="javascript:void(0)" onclick="loadLibrary(\'/\')">根目录</a>';
  var acc = '';
  parts.forEach(function(p){
    acc += '/' + p;
    html += ' <span class="lib-sep">/</span> <a href="javascript:void(0)" onclick="loadLibrary(' + jsArg(acc) + ')">' + esc(p) + '</a>';
  });
  el.innerHTML = html;
}

function enterDir(name){
  var base = libPath === '/' ? '' : libPath;
  loadLibrary(base + '/' + name);
}

function libGoUp(){
  if(libPath === '/' || !libPath) return;
  var parts = libPath.split('/').filter(Boolean);
  parts.pop();
  loadLibrary('/' + parts.join('/'));
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
  var cnt = document.getElementById('libSelCount');
  if(cnt) cnt.textContent = n ? '已选 ' + n + ' 项' : '';
  var btn = document.getElementById('libDelBtn');
  if(btn) btn.disabled = n === 0;
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
  try{
    await showConfirm('确认删除', '将从夸克网盘删除 ' + label + '。' + extra, '删除', '取消');
  }catch(e){ return; }

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
