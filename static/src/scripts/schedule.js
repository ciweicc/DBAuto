// ============ Schedule ============
async function loadSchedule(){
  try{
    var d = SETTINGS_ALL || (SETTINGS_ALL = await apiGet('/api/settings/all'));
    d = d.schedule || d;  // 兼容 /api/schedule 旧响应
    if(d.savepaths){
      APP_PATHS.category_base = d.savepaths.category_base||'/影视';
      APP_PATHS.search = d.savepaths.search||'/批量转存/手动搜索存';
      APP_PATHS.tmdb = d.savepaths.tmdb||'/批量转存/TMDB';
      APP_PATHS.library_root = d.savepaths.library_root||'/';
    }
    if(d.transfer){
      document.getElementById('schedTOn').checked = !!d.transfer.enabled;
      if(d.transfer.time){var p=d.transfer.time.split(':');
        document.getElementById('schedTHour').value=parseInt(p[0])||0;
        document.getElementById('schedTMin').value=parseInt(p[1])||0;}
      if(d.transfer.tasks&&d.transfer.tasks.length>0){
        var savedPaths = new Set();
        d.transfer.tasks.forEach(function(t){savedPaths.add(t.path+'/'+t.type)});
        document.querySelectorAll('#tabManual .chip').forEach(function(el){
          if(savedPaths.has(el.dataset.path+'/'+el.dataset.type))el.classList.add('on');
        });
      }
      if(d.transfer.filters){
        var f = d.transfer.filters;
        document.getElementById('filterRating').value = f.min_rating||0;
        document.getElementById('filterRatingVal').textContent = f.min_rating||0;
        document.getElementById('filterSort').value = f.sort_by||'rating';
        if(f.year_from)document.getElementById('filterYearFrom').value = f.year_from;
        if(f.year_to)document.getElementById('filterYearTo').value = f.year_to;
      }
    }
    if(d.expired_check){document.getElementById('schedEOn').checked=!!d.expired_check.enabled;
      if(d.expired_check.time){var e2=d.expired_check.time.split(':');
        document.getElementById('schedEHour').value=parseInt(e2[0])||0;
        document.getElementById('schedEMin').value=parseInt(e2[1])||0;}
      expiredDirEntries=(d.expired_check.directories&&d.expired_check.directories.length>0)?[...d.expired_check.directories]:[''];renderExpiredDirs();}
    // 上限与手动转存共用同一个输入框（#limit）：它是「本次最多转存几条」，两种执行方式同义
    if(d.transfer && d.transfer.limit){
      var lim=document.getElementById('limit');
      if(lim && !lim.value) lim.value = d.transfer.limit;
    }
    // 调度状态条：上次转存 / 上次检测
    var st = d._status || {};
    var lt = document.getElementById('schedLastTransfer');
    var le = document.getElementById('schedLastExpired');
    if(lt) lt.textContent = st.last_transfer ? String(st.last_transfer).slice(5) : '从未';
    if(le) le.textContent = st.last_expired_check ? String(st.last_expired_check).slice(5) : '从未';
    // 下次执行：由后端 _next_runs 计算（启用中的任务才有值），停用 / 未配置时显示「—」
    var nr = d._next_runs || {};
    _setNextRun('schedTNext', nr.transfer, d.transfer && d.transfer.enabled);
    _setNextRun('schedENext', nr.expired_check, d.expired_check && d.expired_check.enabled);
  }catch(e){showToast('加载调度失败',false)}
}

function autoSaveSchedule(){clearTimeout(autoSaveTimer);autoSaveTimer=setTimeout(saveSchedule,600)}
async function saveSchedule(){
  SETTINGS_ALL = null;  // 失效缓存，保存后 SSE 触发的刷新将拉取最新数据
  var tasks = getSelectedTasks('tabManual');
  var expiredDirs = expiredDirEntries.map(function(d){return d.trim()}).filter(function(d){return d});
  // 若只改了「失效检测目录」这类与 transfer 无关的字段，也必须把 chips 当前选择一并写回 ——
  // 合并后 chips 是唯一的来源，漏写会把用户刚勾的榜单回退成旧值。
var body = {
    transfer:{enabled:document.getElementById('schedTOn').checked,
              time:pad2(document.getElementById('schedTHour').value)+':'+pad2(document.getElementById('schedTMin').value),
              limit:parseInt(document.getElementById('limit').value)||5,tasks:tasks,
              filters:{min_rating:parseFloat(document.getElementById('filterRating').value)||0,
                       sort_by:document.getElementById('filterSort').value,
                       year_from:parseInt(document.getElementById('filterYearFrom').value)||0,
                       year_to:parseInt(document.getElementById('filterYearTo').value)||0,
                       exclude_keywords:[],
                       genre:''}},
    expired_check:{enabled:document.getElementById('schedEOn').checked,
                   time:pad2(document.getElementById('schedEHour').value)+':'+pad2(document.getElementById('schedEMin').value),
                   directories:expiredDirs}
};
  try{var d=await apiPost('/api/schedule',body);if(d.success){showToast('已保存',true)}}
  catch(e){showToast('保存失败',false)}
}
function pad2(n){return String(n).padStart(2,'0')}
// 下次执行时间的展示口径：任务启用且后端算出了时间才显示时间；
// 启用但算不出（如 cron 非法）显示「计算失败」；未启用显示「未启用」——
// 只显示一个「—」会让「没开」和「时间算错了」看起来一样。
function _setNextRun(id, next, enabled){
  var el=document.getElementById(id);
  if(!el) return;
  if(!enabled){ el.textContent='未启用'; return; }
  el.textContent = next ? String(next).slice(5) : '计算失败';
}
function updateFilterRating(){
  document.getElementById('filterRatingVal').textContent = document.getElementById('filterRating').value;
}
function renderExpiredDirs(){
  var el=document.getElementById('expiredDirList');
  el.textContent='';
  expiredDirEntries.forEach(function(d,i){
    var item = document.createElement('div');
    item.className = 'dir-item';
    var input = document.createElement('input');
    input.type = 'text';
    input.value = d;
    input.placeholder = '/影视/电视剧';
    input.addEventListener('change', function(){ expiredDirEntries[i]=this.value; autoSaveSchedule(); });
    var delBtn = document.createElement('span');
    delBtn.className = 'dir-del';
    delBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-delete"/></svg>';
    delBtn.addEventListener('click', function(){ removeExpiredDir(i); });
    item.appendChild(input);
    item.appendChild(delBtn);
    el.appendChild(item);
  });
}
function addExpiredDir(){expiredDirEntries.push('');renderExpiredDirs();autoSaveSchedule()}
function removeExpiredDir(i){expiredDirEntries.splice(i,1);if(!expiredDirEntries.length)expiredDirEntries=[''];renderExpiredDirs();autoSaveSchedule()}

