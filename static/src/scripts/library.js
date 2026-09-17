// ============ 媒体库（双向管理，issue #27） ============
// 目标：把「夸克网盘真实文件」与「QAS 转存任务」显式展示出来，并允许在网盘侧删除后
// 联动清理 DBAuto 的转存历史——否则网盘删了、本地仍判为「已转存」，重新转存会被跳过。
//
// 后端接口：
//   GET  /api/qas/tasks          QAS tasklist
//   GET  /api/qas/library?path=  网盘目录
//   POST /api/qas/delete         {entries:[{fid,name,title?}], sync_history:true}

var libPath = '/';
var libEntries = [];
var libSelected = {};
var qasTasks = [];

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
  loadQasTasks();
  loadLibrary(libPath);
}

// ---------- QAS 任务 ----------

async function loadQasTasks(){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">加载中...</div></div>';
  try{
    var res = await apiGet('/api/qas/tasks');
    if(!res.success){
      box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(res.message || '请检查 QAS 配置') + '</div></div>';
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
  }catch(e){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(e.message) + '</div></div>';
  }
}

// ---------- 网盘文件 ----------

async function loadLibrary(path){
  var box = document.getElementById('libList');
  if(!box) return;
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">加载中...</div></div>';
  libSelected = {}; libEntries = [];
  try{
    var res = await apiGet('/api/qas/library?path=' + encodeURIComponent(path || '/'));
    if(!res.success){
      box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(res.message || '请检查 QAS 配置') + '</div></div>';
      renderBreadcrumb(path || '/');
      return;
    }
    libPath = path || '/';
    libEntries = res.entries || [];
    libEntries.forEach(function(it, i){
      it._key = it.fid != null ? String(it.fid) : ('p' + i);
    });
    renderBreadcrumb(libPath);
    document.getElementById('libCount').textContent = libEntries.length ? '共 ' + libEntries.length + ' 项' : '';
    document.getElementById('libUpBtn').disabled = (libPath === '/' || libPath === '');
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
  }catch(e){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">读取失败</div><div class="empty-desc">' + esc(e.message) + '</div></div>';
  }
  updateLibActionBar();
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
  }
  loadLibrary(libPath);
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
