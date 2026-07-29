// ============ Auth ============
var TOKEN = localStorage.getItem('auth_token')||'';
if(!TOKEN){location.href='/login.html'}
function authHeaders(){return{'X-Auth-Token':TOKEN}}
async function apiGet(url,retries=2,signal){
for(let i=0;i<=retries;i++){
try{
var r = await fetch(url,{headers:authHeaders(),signal:signal});
if(r.status===401){TOKEN='';localStorage.removeItem('auth_token');showToast('登录已过期，正在跳转...',false);setTimeout(function(){location.href='/login.html'},800);throw new Error('401')}
return r.json()
}catch(e){
if(e.name==='AbortError')throw e;
if(e.message==='401')throw e;
if(i<retries){await new Promise(r=>setTimeout(r,1000*(i+1)))}
else throw e
}
}
}
async function apiPost(url,body,retries=2){
for(let i=0;i<=retries;i++){
try{
var r = await fetch(url,{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify(body)});
if(r.status===401){TOKEN='';localStorage.removeItem('auth_token');showToast('登录已过期，正在跳转...',false);setTimeout(function(){location.href='/login.html'},800);throw new Error('401')}
return r.json()
}catch(e){
if(e.message==='401')throw e;
if(i<retries){await new Promise(r=>setTimeout(r,1000*(i+1)))}
else throw e
}
}
}

// ============ Animation Utils ============
function easeOutCubic(t){return 1-Math.pow(1-t,3)}
function animateNumber(el, target, duration){
  duration = duration||800;
  var start = 0;
  try{start = parseInt(el.textContent)||0}catch(e){start=0}
  if(start===target)return;
  var startTime = null;
  function step(ts){
    if(!startTime)startTime=ts;
    var p = Math.min((ts-startTime)/duration, 1);
    var eased = easeOutCubic(p);
    var val = Math.round(start + (target-start)*eased);
    el.textContent = val;
    if(p<1)requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ============ Theme ============
function initTheme(){
  var t = localStorage.getItem('theme')||'dark';
  document.documentElement.setAttribute('data-theme',t);
  var icon = t==='dark'?'icon-moon':'icon-sun';
  var themeBtnEl = document.getElementById('themeBtn');
  themeBtnEl.textContent='';
  var themeSvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  themeSvg.setAttribute('width','18');
  themeSvg.setAttribute('height','18');
  themeSvg.setAttribute('viewBox','0 0 24 24');
  themeSvg.setAttribute('fill','none');
  themeSvg.setAttribute('stroke','currentColor');
  themeSvg.setAttribute('stroke-width','2');
  var themeUse = document.createElementNS('http://www.w3.org/2000/svg','use');
  themeUse.setAttribute('href','#'+icon);
  themeSvg.appendChild(themeUse);
  themeBtnEl.appendChild(themeSvg);
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  localStorage.setItem('theme',next);
  var icon = next==='dark'?'icon-moon':'icon-sun';
  var themeBtnEl = document.getElementById('themeBtn');
  themeBtnEl.textContent='';
  var themeSvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  themeSvg.setAttribute('width','18');
  themeSvg.setAttribute('height','18');
  themeSvg.setAttribute('viewBox','0 0 24 24');
  themeSvg.setAttribute('fill','none');
  themeSvg.setAttribute('stroke','currentColor');
  themeSvg.setAttribute('stroke-width','2');
  var themeUse = document.createElementNS('http://www.w3.org/2000/svg','use');
  themeUse.setAttribute('href','#'+icon);
  themeSvg.appendChild(themeUse);
  themeBtnEl.appendChild(themeSvg);
}

// ============ Global ============
var C = {}, currentTab = 'manual', execHistoryData = [], execHistoryFilter = 'all';
var expiredDirEntries = [], autoSaveTimer = null, logPollTimer = null;
var logBefore = [], logFilter = 'all', logPaused = false;
var logPollInterval = 0;
var sseConnected = false;
var LOG_BEFORE_MAX = 500;

// ============ Toast ============
function showToast(msg,ok,duration){
  var container=document.getElementById('toastContainer');
  var t=document.createElement('div');
  t.className='toast '+(ok?'ok':'err')+' show';
  var icon=ok?'<span class="toast-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-check-circle"/></svg></span>':'<span class="toast-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-x-circle"/></svg></span>';
  var iconSpan = document.createElement('span');
  iconSpan.className = 'toast-icon';
  iconSpan.innerHTML = ok
    ?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-check-circle"/></svg>'
    :'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-x-circle"/></svg>';
  t.appendChild(iconSpan);
  var msgSpan=document.createElement('span');
  msgSpan.textContent=msg;
  t.appendChild(msgSpan);
  container.appendChild(t);
  var dur=duration||(ok?2500:4000);
  setTimeout(function(){
    t.classList.add('toast-out');
    setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t)},300);
  },dur);
}

// ============ Confirm Dialog ============
var _confirmResolve=null;
function showConfirm(title,desc,confirmText,cancelText){
  return new Promise(function(resolve){
    _confirmResolve=resolve;
    document.getElementById('confirmTitle').textContent=title||'确认';
    document.getElementById('confirmDesc').textContent=desc||'';
    document.getElementById('confirmOk').textContent=confirmText||'确认';
    document.getElementById('confirmCancel').textContent=cancelText||'取消';
    document.getElementById('confirmOverlay').classList.add('show');
  });
}
function resolveConfirm(result){
  document.getElementById('confirmOverlay').classList.remove('show');
  if(_confirmResolve){_confirmResolve(result);_confirmResolve=null}
}

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

// ============ Categories ============
function _makeChip(id, path, type, group, gname, label, onClick) {
  var chip = document.createElement('span');
  chip.className = 'chip';
  chip.id = 'c_' + id;
  chip.dataset.id = id;
  chip.dataset.path = path;
  chip.dataset.type = type;
  chip.dataset.group = group;
  chip.dataset.gname = gname;
  chip.textContent = label;
  chip.addEventListener('click', function() { onClick(chip); });
  return chip;
}

function parseCategories(){
  for(var gk in C){
    var g = C[gk];
    var container = null;
    if(gk==='movie')container=document.getElementById('catsMovie');
    else if(gk==='tv')container=document.getElementById('catsTV');
    else container=document.getElementById('catsVariety');
    if(!container)continue;
    container.textContent='';
    for(var sn in g.subs){
      var s = g.subs[sn];
      var subTitle = document.createElement('div');
      subTitle.className = 'sub-title';
      subTitle.textContent = sn + ' ';
      var selAll = document.createElement('span');
      selAll.className = 'sel-all';
      selAll.textContent = '全选';
      selAll.addEventListener('click', function() { toggleSub(this); });
      subTitle.appendChild(selAll);
      container.appendChild(subTitle);
      var chipsDiv = document.createElement('div');
      chipsDiv.className = 'chips';
      for(var ti=0; ti<s.types.length; ti++){
        var t = s.types[ti];
        var id = gk+'_'+sn.replace(/\s/g,'_')+'_'+t.replace(/\s/g,'_');
        chipsDiv.appendChild(_makeChip(id, s.path, t, gk, g.name, t, toggleChip));
      }
      container.appendChild(chipsDiv);
    }
  }
}

function parseSchedCats(){
  for(var gk in C){
    var g = C[gk];
    var container = null;
    if(gk==='movie')container=document.getElementById('schedCatsMovie');
    else if(gk==='tv')container=document.getElementById('schedCatsTV');
    else container=document.getElementById('schedCatsVariety');
    if(!container)continue;
    container.textContent='';
    for(var sn in g.subs){
      var s = g.subs[sn];
      var subTitle = document.createElement('div');
      subTitle.className = 'sub-title';
      subTitle.textContent = sn + ' ';
      var selAll = document.createElement('span');
      selAll.className = 'sel-all';
      selAll.textContent = '全选';
      selAll.addEventListener('click', function() { toggleSubSched(this); });
      subTitle.appendChild(selAll);
      container.appendChild(subTitle);
      var chipsDiv = document.createElement('div');
      chipsDiv.className = 'chips';
      for(var ti=0; ti<s.types.length; ti++){
        var t = s.types[ti];
        var id = 'sch_'+gk+'_'+sn.replace(/\s/g,'_')+'_'+t.replace(/\s/g,'_');
        chipsDiv.appendChild(_makeChip(id, s.path, t, gk, g.name, t, toggleSchedChip));
      }
      container.appendChild(chipsDiv);
    }
  }
}

function toggleChip(el){el.classList.toggle('on')}
function toggleSchedChip(el){el.classList.toggle('on');autoSaveSchedule()}
function toggleWishCat(el){el.classList.toggle('on')}
function toggleWishEnabled(el){el.classList.toggle('on')}
function renderWishAccounts(accounts){
var el=document.getElementById('wishAccountsList');
el.innerHTML='';
if(!accounts||!accounts.length)return;
for(var i=0;i<accounts.length;i++){el.appendChild(_makeWishAccRow(i,accounts[i]||{}))}
}
function _makeWishAccRow(i,a){
var row=document.createElement('div');
row.className='wish-acc-row';
row.style.cssText='border:1px solid var(--border);border-radius:12px;padding:12px;background:rgba(255,255,255,.02)';
var headerDiv = document.createElement('div');
headerDiv.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px';
var headerSpan = document.createElement('span');
headerSpan.style.cssText = 'font-size:13px;font-weight:600;color:var(--text2);font-family:var(--font-display)';
headerSpan.textContent = '账号 '+(i+1);
var removeBtn = document.createElement('button');
removeBtn.className = 'btn btn-sm btn-outline';
removeBtn.style.color = 'var(--text3)';
removeBtn.textContent = '移除';
removeBtn.addEventListener('click', function(){ removeWishAccount(this); });
headerDiv.appendChild(headerSpan);
headerDiv.appendChild(removeBtn);

function _makeFormGroup(labelText, inputClass, placeholder, value, type) {
  var group = document.createElement('div');
  group.className = 'form-group';
  group.style.marginBottom = '8px';
  var label = document.createElement('label');
  label.textContent = labelText;
  var input = document.createElement('input');
  input.type = type || 'text';
  input.className = inputClass;
  input.placeholder = placeholder;
  if(value) input.value = value;
  group.appendChild(label);
  group.appendChild(input);
  return group;
}

row.appendChild(headerDiv);
row.appendChild(_makeFormGroup('备注','wish-acc-name','可选备注名',a.name||''));
row.appendChild(_makeFormGroup('豆瓣用户 ID','wish-acc-uid','豆瓣个人主页 URL 中的 ID',a.uid||''));
var cookieGroup = _makeFormGroup('豆瓣 Cookie','wish-acc-cookie','留空不修改','','password');
cookieGroup.style.marginBottom = '0';
row.appendChild(cookieGroup);
return row;
}
function addWishAccount(){
var el=document.getElementById('wishAccountsList');
el.appendChild(_makeWishAccRow(el.children.length,{}));
}
function removeWishAccount(btn){btn.closest('.wish-acc-row').remove()}
function collectWishAccounts(){
var accounts=[];
document.querySelectorAll('#wishAccountsList .wish-acc-row').forEach(function(row){
var name=row.querySelector('.wish-acc-name').value.trim();
var uid=row.querySelector('.wish-acc-uid').value.trim();
var cookie=row.querySelector('.wish-acc-cookie').value;
if(uid)accounts.push({uid:uid,cookie:cookie||'***',name:name});
});
return accounts;
}

function toggleSub(btn){
  var chips = btn.parentElement.nextElementSibling.querySelectorAll('.chip');
  var all = true;
  for(var i=0;i<chips.length;i++){if(!chips[i].classList.contains('on')){all=false;break}}
  for(var i=0;i<chips.length;i++){if(all)chips[i].classList.remove('on');else chips[i].classList.add('on')}
}
function toggleSubSched(btn){
  var chips = btn.parentElement.nextElementSibling.querySelectorAll('.chip');
  var all = true;
  for(var i=0;i<chips.length;i++){if(!chips[i].classList.contains('on')){all=false;break}}
  for(var i=0;i<chips.length;i++){if(all)chips[i].classList.remove('on');else chips[i].classList.add('on')}
  autoSaveSchedule();
}

function getSelectedTasks(prefix){
  var tasks=[], seen=new Set();
  document.querySelectorAll('#'+prefix+' .chip.on').forEach(function(el){
    var k = el.dataset.path+'/'+el.dataset.type;
    if(seen.has(k))return; seen.add(k);
    tasks.push({path:el.dataset.path, type:el.dataset.type, savepath:'/影视/'+el.dataset.gname, category:el.dataset.group});
  });
  return tasks;
}

function syncToSchedule(){
  document.querySelectorAll('#tabSchedule .chip').forEach(function(c){c.classList.remove('on')});
  var manual = document.querySelectorAll('#tabManual .chip.on');
  var paths = new Set();
  manual.forEach(function(m){
    var key = m.dataset.path+'/'+m.dataset.type;
    paths.add(key);
  });
  document.querySelectorAll('#tabSchedule .chip').forEach(function(c){
    var key = c.dataset.path+'/'+c.dataset.type;
    if(paths.has(key))c.classList.add('on');
  });
  autoSaveSchedule();
  showToast('已同步到定时任务',true);
}

// ============ Transfer ============
async function startTransfer(){
  var tasks = getSelectedTasks('tabManual');
  if(!tasks.length){showToast('请至少选择一个榜单',false);return}
  var limit = parseInt(document.getElementById('limit').value)||5;
  var filters = {
    min_rating: parseFloat(document.getElementById('manualFilterRating').value)||0,
    sort_by: document.getElementById('manualFilterSort').value,
    year_from: parseInt(document.getElementById('manualFilterYearFrom').value)||0,
    year_to: parseInt(document.getElementById('manualFilterYearTo').value)||0
  };
  // 提前清空日志，避免 SSE 早期日志被竞态清除
  document.getElementById('logCard').style.display='block';
  logBefore=[]; document.getElementById('log').textContent='';
  addLog('[开始转存]');
  try{
    var d = await apiPost('/api/transfer',{tasks:tasks,limit:limit,filters:filters});
    if(d.success){
      document.getElementById('stopBtn').style.display='inline-block';
      startLogPoll(3000);
    }
    else{
      document.getElementById('stopBtn').style.display='none';
      if(d.conflict){addLog('已有任务在运行，恢复监控...');checkRunningStatus()}
      else addLog(d.message||'启动失败');
    }
  }catch(e){addLog('请求失败: '+e.message);document.getElementById('stopBtn').style.display='none'}
}

async function stopTransfer(){
  try{await apiPost('/api/stop')}catch(e){}
  addLog('任务已终止');
  document.getElementById('stopBtn').style.display='none';
  if(logPollTimer){clearInterval(logPollTimer);logPollTimer=null;logPollInterval=0}
}

// ============ Log ============
function esc(s){
  if(s==null)return'';
  var d=document.createElement('div');
  d.textContent=String(s);
  return d.innerHTML.replace(/'/g,'&#39;')
}

function addLog(line){
  logBefore.push(line);
  if(logBefore.length>LOG_BEFORE_MAX)logBefore.shift();
  if(matchFilter(line)) appendLine(line);
}
function matchFilter(line){
  if(logFilter==='all')return true;
  if(logFilter==='ok')return line.indexOf('成功')>=0||line.indexOf('完成')>=0||line.indexOf('已执行')>=0;
  if(logFilter==='skip')return line.indexOf('跳过')>=0||line.indexOf('已存在')>=0||line.indexOf('exists')>=0;
  if(logFilter==='fail')return line.indexOf('失败')>=0||line.indexOf('未找到')>=0||line.indexOf('错误')>=0||line.indexOf('not found')>=0||line.indexOf('error')>=0;
  return true;
}
function renderLog(){
  var el = document.getElementById('log');
  el.textContent='';
  for(var i=0;i<logBefore.length;i++){if(matchFilter(logBefore[i])) appendLine(logBefore[i])}
  if(!logPaused){el.scrollTop=el.scrollHeight}
}
function appendLine(line){
  var el = document.getElementById('log');
  var cls='inf';
  if(line.indexOf('成功')>=0||line.indexOf('完成')>=0||line.indexOf('已执行')>=0)cls='ok';
  else if(line.indexOf('失败')>=0||line.indexOf('未找到')>=0||line.indexOf('not found')>=0||line.indexOf('错误')>=0||line.indexOf('error')>=0)cls='er';
  else if(line.indexOf('跳过')>=0||line.indexOf('已存在')>=0||line.indexOf('exists')>=0)cls='sk';
  else if(line.indexOf('找到')>=0)cls='inf';
  var lineEl = document.createElement('div');
  lineEl.className = 'log-line ' + cls;
  lineEl.textContent = line;
  el.appendChild(lineEl);
  if(!logPaused)el.scrollTop=el.scrollHeight;
}
function setLogFilter(btn,type){
  logFilter=type;
  document.querySelectorAll('#logCard .filter-tab').forEach(function(t){t.classList.remove('active')});
  btn.classList.add('active');
  renderLog();
}
function togglePause(){
  logPaused=!logPaused;
  var pauseIcon = logPaused ? 'icon-play' : 'icon-pause';
  var pauseBtnEl = document.getElementById('pauseBtn');
  pauseBtnEl.textContent='';
  var pauseSvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  pauseSvg.setAttribute('width','14');
  pauseSvg.setAttribute('height','14');
  pauseSvg.setAttribute('viewBox','0 0 24 24');
  pauseSvg.setAttribute('fill','none');
  pauseSvg.setAttribute('stroke','currentColor');
  pauseSvg.setAttribute('stroke-width','2');
  var pauseUse = document.createElementNS('http://www.w3.org/2000/svg','use');
  pauseUse.setAttribute('href','#'+pauseIcon);
  pauseSvg.appendChild(pauseUse);
  pauseBtnEl.appendChild(pauseSvg);
}

function startLogPoll(interval, initLen, skipFirstSync){
  if(logPollTimer)clearInterval(logPollTimer);
  var pollInterval=interval||3000;
  var lastProgressLen=initLen||0;
  var firstSyncSkip=skipFirstSync||false;
  logPollInterval=pollInterval;
  logPollTimer = setInterval(async function(){
    if(document.getElementById('logCard').style.display==='none')return;
    try{
      var d = await apiGet('/api/transfer/status');
      // 同步日志进度（仅当 SSE 断连时作为降级方案，避免与 SSE 重复）
      if(!sseConnected&&d.progress&&d.progress.length>0){
        if(firstSyncSkip){
          // SSE 刚断连，仅对齐进度游标，不重复添加已有日志
          lastProgressLen=d.progress.length;
          firstSyncSkip=false;
        }else if(d.progress.length>lastProgressLen){
          for(var i=lastProgressLen;i<d.progress.length;i++)addLog(d.progress[i]);
          lastProgressLen=d.progress.length;
        }
      }
      if(d.stats){
        document.getElementById('stSearched').textContent=d.stats.searched||0;
        document.getElementById('stOK').textContent=d.stats.ok||0;
        document.getElementById('stSkip').textContent=d.stats.skipped||0;
        document.getElementById('stFail').textContent=d.stats.failed||0;
        document.getElementById('stTotal').textContent=d.stats.total||0;
        updateProgress(d.stats);
      }
      if(!d.running&&logPollTimer){clearInterval(logPollTimer);logPollTimer=null;logPollInterval=0;
        document.getElementById('stopBtn').style.display='none';addLog('全部完成');}
    }catch(e){}
  },pollInterval);
}
function updateProgress(stats){
  var total=(stats.total||0), done=(stats.ok||0)+(stats.skipped||0)+(stats.failed||0);
  var pct=total>0?Math.round(done/total*100):0;
  var fill=document.getElementById('progressFill');if(fill)fill.style.width=pct+'%';
  var ringFill=document.getElementById('ringFill');
  var ringText=document.getElementById('ringText');
  var ringSub=document.getElementById('ringSubtitle');
  if(ringFill){
    var circumference=2*Math.PI*18;
    var offset=circumference-(pct/100)*circumference;
    ringFill.style.strokeDasharray=circumference;
    ringFill.style.strokeDashoffset=offset;
  }
  if(ringText)ringText.textContent=pct+'%';
  if(ringSub){
    if(total>0){
      ringSub.textContent='已完成 '+done+' / '+total+' 个任务';
    }else{
      ringSub.textContent='正在搜索资源...';
    }
  }
}

async function checkRunningStatus(){
  try{
    var d = await apiGet('/api/transfer/status');
    if(d.running){
      document.getElementById('logCard').style.display='block';
      document.getElementById('stopBtn').style.display='inline-block';
      var initLen=0;
      if(d.progress&&d.progress.length>0){logBefore=[];for(var i=0;i<d.progress.length;i++)addLog(d.progress[i]);initLen=d.progress.length}
      startLogPoll(3000, initLen);
    }
  }catch(e){showToast('检查运行状态失败',false)}
}
async function checkExpired(){
  if(logPollTimer){clearInterval(logPollTimer);logPollTimer=null;logPollInterval=0}
  document.getElementById('logCard').style.display='block'; logBefore=[]; document.getElementById('log').textContent='';
  addLog('正在检测失效链接...');
  try{
    var d = await apiGet('/api/check_expired');
    if(d.expired&&d.expired.length>0){
      addLog('发现 '+d.expired.length+' 个失效链接');
      for(var i=0;i<d.expired.length;i++)addLog('  '+d.expired[i].taskname);
      addLog('开始搜索并替换失效链接...');
      var fd = await apiGet('/api/fix_expired');
      if(fd.success){
        document.getElementById('stopBtn').style.display='inline-block';
        startLogPoll(3000);
      }else{
        if(fd.conflict){
          addLog('已有任务在运行，恢复监控...');
          checkRunningStatus();
        }else{
          addLog('启动修复失败: '+(fd.message||'未知错误'));
        }
      }
    }else addLog('所有链接正常');
  }catch(e){addLog('检测失败: '+e.message)}
}

// ============ Schedule ============
async function loadSchedule(){
  try{
    var d = await apiGet('/api/schedule');
    if(d.transfer){
      document.getElementById('schedTOn').checked = !!d.transfer.enabled;
      document.getElementById('schedLimit').value = d.transfer.limit||5;
      if(d.transfer.time){var p=d.transfer.time.split(':');
        document.getElementById('schedTHour').value=parseInt(p[0])||0;
        document.getElementById('schedTMin').value=parseInt(p[1])||0;}
      if(d.transfer.tasks&&d.transfer.tasks.length>0){
        var savedPaths = new Set();
        d.transfer.tasks.forEach(function(t){savedPaths.add(t.path+'/'+t.type)});
        document.querySelectorAll('#tabSchedule .chip').forEach(function(el){
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
  }catch(e){showToast('加载调度失败',false)}
}

function autoSaveSchedule(){clearTimeout(autoSaveTimer);autoSaveTimer=setTimeout(saveSchedule,600)}
async function saveSchedule(){
  var tasks = getSelectedTasks('tabSchedule');
  var expiredDirs = expiredDirEntries.map(function(d){return d.trim()}).filter(function(d){return d});
var body = {
    transfer:{enabled:document.getElementById('schedTOn').checked,
              time:pad2(document.getElementById('schedTHour').value)+':'+pad2(document.getElementById('schedTMin').value),
              limit:parseInt(document.getElementById('schedLimit').value)||5,tasks:tasks,
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
function updateFilterRating(){
  document.getElementById('filterRatingVal').textContent = document.getElementById('filterRating').value;
}
function updateManualFilterRating(){
  document.getElementById('manualFilterRatingVal').textContent = document.getElementById('manualFilterRating').value;
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

// ============ Exec History ============
var histPage=0, histMore=true;
async function loadExecHistory(){
  histPage=1;histMore=true;
  var el=document.getElementById('execHistoryList');
  el.textContent='';
  for(var i=0;i<5;i++){
    var sk = document.createElement('div');
    sk.className = 'skeleton-row';
    el.appendChild(sk);
  }
  try{
    var data = await apiGet('/api/exec_history?limit=50');
    execHistoryData = data.items || data || [];
    if(execHistoryData.length < 50) histMore=false;
    renderExecHistory();
  }catch(e){
    el.textContent='';
    el.appendChild(_makeEmptyState('icon-x-circle','加载失败了',esc(e.message||'网络连接可能有问题')+'<br>请检查网络后刷新页面重试'));
  }
}
async function loadMoreHistory(){
  if(!histMore)return; histPage++;
  var btn=document.querySelector('#execHistoryList .btn-outline');
  if(btn){btn.disabled=true;btn.textContent='加载中...'}
  try{
    var data = await apiGet('/api/exec_history?limit=50&page='+histPage);
    var more = data.items || data || [];
    if(more.length===0){histMore=false;renderExecHistory();return}
    execHistoryData = execHistoryData.concat(more);
    if(more.length<50)histMore=false;
    renderExecHistory();
  }catch(e){
    if(btn){btn.disabled=false;btn.textContent='加载更多'}
    showToast('加载更多历史失败',false);
    histPage--;
  }
}
function _makeEmptyState(iconId, title, descHTML) {
  var wrapper = document.createElement('div');
  wrapper.className = 'empty-state';
  var iconDiv = document.createElement('div');
  iconDiv.className = 'empty-icon';
  iconDiv.innerHTML = '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><use href="#'+iconId+'"/></svg>';
  var titleDiv = document.createElement('div');
  titleDiv.className = 'empty-title';
  titleDiv.textContent = title;
  var descDiv = document.createElement('div');
  descDiv.className = 'empty-desc';
  descDiv.innerHTML = descHTML;
  wrapper.appendChild(iconDiv);
  wrapper.appendChild(titleDiv);
  wrapper.appendChild(descDiv);
  return wrapper;
}

function renderExecHistory(){
  var el = document.getElementById('execHistoryList');
  var filtered = execHistoryFilter==='all'?execHistoryData.filter(function(h){return h.type!=='history'&&h.type!=='schedule'}).slice():execHistoryData.filter(function(h){return h.type===execHistoryFilter});
  el.textContent='';
  if(!filtered.length){
    el.appendChild(_makeEmptyState('icon-clock','还没有执行记录','去「手动转存」选择一个榜单开始吧<br>每次转存和检测都会记录在这里'));
    return;
  }
  filtered.reverse().forEach(function(h){
    var iconId='icon-transfer',cls='transfer',tn='转存';
    if(h.type==='expired_check'){iconId='icon-refresh';cls='expired';tn='检测'}
    else if(h.type==='config'){iconId='icon-settings';cls='config';tn='配置'}
    var hasDetail = h.data && (h.data.results || h.data.expired);
    var hid = String(h.id);

    var item = document.createElement('div');
    item.className = 'hist-item';
    if(hasDetail) item.style.cursor = 'pointer';
    item.id = 'hist_' + hid;

    var iconDiv = document.createElement('div');
    iconDiv.className = 'hist-icon ' + cls;
    iconDiv.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#'+iconId+'"/></svg>';

    var infoDiv = document.createElement('div');
    infoDiv.className = 'hist-info';
    var titleDiv = document.createElement('div');
    titleDiv.className = 'hist-title';
    titleDiv.textContent = h.detail;
    var metaDiv = document.createElement('div');
    metaDiv.className = 'hist-meta';
    metaDiv.textContent = tn + (hasDetail ? ' · 点击查看详情' : '');
    infoDiv.appendChild(titleDiv);
    infoDiv.appendChild(metaDiv);

    var timeDiv = document.createElement('div');
    timeDiv.className = 'hist-time';
    timeDiv.textContent = h.time;

    item.appendChild(iconDiv);
    item.appendChild(infoDiv);
    item.appendChild(timeDiv);
    item.addEventListener('click', function(){ toggleHistDetail(hid); });
    el.appendChild(item);

    if(hasDetail){
      var detailDiv = document.createElement('div');
      detailDiv.className = 'hist-detail';
      detailDiv.id = 'hist_detail_' + hid;
      detailDiv.style.cssText = 'display:none;padding:0 16px 16px 60px;border-bottom:1px solid var(--border);background:rgba(255,255,255,.02)';
      detailDiv.innerHTML = renderHistDetailContent(h);
      el.appendChild(detailDiv);
    }
  });
  if(histMore){
    var moreDiv = document.createElement('div');
    moreDiv.style.cssText = 'text-align:center;padding:12px;margin-top:6px';
    var moreBtn = document.createElement('button');
    moreBtn.className = 'btn btn-outline btn-sm';
    moreBtn.textContent = '加载更多';
    moreBtn.addEventListener('click', loadMoreHistory);
    moreDiv.appendChild(moreBtn);
    el.appendChild(moreDiv);
  }
}
function renderHistDetailContent(h){
  if(!h.data) return '<div style=\"color:var(--text3);font-size:13px\">无详情</div>';
  if(h.type==='transfer' && h.data.results){
    var items = h.data.results;
    if(!items || !items.length) return '<div style=\"color:var(--text3);font-size:13px\">无数据</div>';
    return '<div style=\"max-height:300px;overflow-y:auto\">'+
      items.map(function(r){
        var st = r.status;
        var stText='未知', stClr='var(--text2)';
        if(st==='ok'||st==='done'){stText='成功';stClr='#30d158'}
        else if(st==='skipped'||st==='exists'){stText='跳过';stClr='var(--text3)'}
        else if(st==='not_found'){stText='未找到';stClr='#ff9f0a'}
        else if(st==='error'||st==='fail'){stText='失败';stClr='#ff453a'}
        var cat = r.category ? '<span style=\"padding:1px 6px;border-radius:4px;background:rgba(10,132,255,.1);color:var(--accent);font-size:10px;margin-right:6px\">'+esc(r.category)+'</span>' : '';
        return '<div style=\"padding:6px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:8px\">'+
          '<div style=\"flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px\">'+cat+esc(r.title)+'</div>'+
          '<span style=\"font-size:12px;color:'+stClr+';flex-shrink:0\">'+stText+'</span>'+
          '</div>';
      }).join('')+
      '</div>';
  }
  if(h.type==='expired_check' && h.data.expired){
    var items = h.data.expired;
    if(!items || !items.length) return '<div style=\"color:#30d158;font-size:13px\">所有链接正常</div>';
    return '<div style=\"max-height:300px;overflow-y:auto\">'+
      items.map(function(r){
        return '<div style=\"padding:6px 0;border-bottom:1px solid var(--border);font-size:13px\">'+
          '<div style=\"color:#ff453a\">'+esc(r.title||'未知')+'</div>'+
          '<div style=\"color:var(--text3);font-size:12px;margin-top:2px\">'+esc(r.path||'')+'</div>'+
          (r.msg?'<div style=\"color:var(--text2);font-size:12px;margin-top:2px\">'+esc(r.msg)+'</div>':'')+
          '</div>';
      }).join('')+
      '</div>';
  }
  return '<div style=\"color:var(--text3);font-size:13px\">无详情</div>';
}
function toggleHistDetail(id){
  var detailEl = document.getElementById('hist_detail_'+id);
  if(!detailEl) return;
  var itemEl = document.getElementById('hist_'+id);
  if(detailEl.style.display === 'none'){
    detailEl.style.display = 'block';
    if(itemEl) itemEl.style.borderBottom = 'none';
  }else{
    detailEl.style.display = 'none';
    if(itemEl) itemEl.style.borderBottom = '';
  }
}
function filterHist(type,btn){
  execHistoryFilter=type;
  document.querySelectorAll('#tabHistory .filter-tab').forEach(function(t){t.classList.remove('active')});
  btn.classList.add('active');
  renderExecHistory();
}
async function exportHistory(){
  try{var r=await fetch('/api/history/export',{headers:authHeaders()});
    if(r.status===401){TOKEN='';localStorage.removeItem('auth_token');location.href='/login.html';return}
    if(!r.ok){showToast('导出失败: '+r.status,false);return}
    var blob=await r.blob();
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='exec_history.json';a.click();
    URL.revokeObjectURL(a.href);
  }
  catch(e){showToast('导出失败',false)}
}
async function clearExecHistory(){
  var ok=await showConfirm('清空执行历史','确定要清空所有执行历史吗？此操作不可撤销。','清空','取消');
  if(!ok)return;
  try{
    var d=await apiPost('/api/exec_history/manage',{action:'clear'});
    if(d.success){
      execHistoryData=[];histMore=false;renderExecHistory();
      showToast('已清空执行历史',true);
    }else showToast(d.message||'清空失败',false);
  }catch(e){showToast('清空失败',false)}
}

// ============ Settings ============
function openSettings(){switchTab('settings')}
async function loadConfig(){
  try{var cfg=await apiGet('/api/config');
    document.getElementById('cfg_pansou').value=cfg.pansou||'';
    document.getElementById('cfg_qas').value=cfg.qas||'';
document.getElementById('cfg_qas_token').value='';
document.getElementById('cfg_auth_user').value=cfg.auth_user||'';
document.getElementById('cfg_auth_pass').value='';
document.getElementById('cfg_tmdb_api_key').value='';
document.getElementById('cfg_tmdb_base_url').value=cfg.tmdb_base_url||'';
// 加载想看同步设置
try{var s=await apiGet('/api/schedule');
if(s.douban_wish){
document.getElementById('cfg_wish_savepath').value=s.douban_wish.savepath||'/批量转存/想看';
var wishCats=s.douban_wish.category;
if(typeof wishCats==='string')wishCats=[wishCats];
if(!Array.isArray(wishCats))wishCats=['movie'];
document.querySelectorAll('#wishCategoryChips .chip').forEach(function(c){c.classList.toggle('on',wishCats.indexOf(c.dataset.cat)!==-1)});
document.getElementById('cfg_wish_enabled_chip').classList.toggle('on',!!s.douban_wish.enabled);
var wishAccounts=s.douban_wish.accounts||[];
if(!wishAccounts.length&&cfg.douban_uid){wishAccounts=[{uid:cfg.douban_uid,cookie:'',name:''}]}
renderWishAccounts(wishAccounts);
}
}catch(e){}

  }catch(e){showToast('加载配置失败',false)}
}
async function refreshDoubanCache(){
try{var d=await apiGet('/api/refresh_douban');showToast(d.message||'已刷新',true)}
catch(e){showToast('刷新失败',false)}
}
async function saveConfig(silent=false){
var cfg={pansou:document.getElementById('cfg_pansou').value.trim(),
qas:document.getElementById('cfg_qas').value.trim(),
qas_token:document.getElementById('cfg_qas_token').value,
auth_user:document.getElementById('cfg_auth_user').value.trim(),
auth_pass:document.getElementById('cfg_auth_pass').value,
tmdb_api_key:document.getElementById('cfg_tmdb_api_key').value,
tmdb_base_url:document.getElementById('cfg_tmdb_base_url').value.trim()};
try{
var d=await apiPost('/api/config',cfg);
if(d.success){if(!silent)showToast('设置已保存',true)}
else showToast(d.message||'保存失败',false);
// 保存想看同步设置
var wishCats=[];document.querySelectorAll('#wishCategoryChips .chip.on').forEach(function(c){wishCats.push(c.dataset.cat)});
var wishAccounts=collectWishAccounts();
var wishCfg={action:'save',douban_wish:{enabled:document.getElementById('cfg_wish_enabled_chip').classList.contains('on'),savepath:document.getElementById('cfg_wish_savepath').value.trim(),category:wishCats.length?wishCats:['movie'],accounts:wishAccounts}};
await apiPost('/api/schedule',wishCfg);
}catch(e){showToast('保存失败',false)}
}

// ============ TMDB Page ============
var tmdbState = {
  page: 1,
  total_pages: 1,
  media_type: 'movie',
  list_type: 'trending',
  genre_id: 0,
  items: [],
  selected: {},
  options: null,
  genres: [],
  initialized: false
};

async function initTmdbPage(){
  if(!tmdbState.initialized){
    // 加载选项
    try{
      var opts = await apiGet('/api/tmdb/options');
      tmdbState.options = opts;
      // 填充地区
      var regionSel = document.getElementById('tmdbRegion');
      regionSel.innerHTML = opts.regions.map(function(r){return '<option value="'+r.code+'">'+r.name+'</option>'}).join('');
      // 填充列表类型
      updateTmdbListTypeOptions(opts.movie_list_types);
      tmdbState.initialized = true;
    }catch(e){
      console.error('TMDB init error', e);
    }
  }
  // 检查 API Key
  try{
    var cfg = await apiGet('/api/config');
    if(cfg.tmdb_api_key && cfg.tmdb_api_key !== '***'){
      document.getElementById('tmdbKeyWarning').style.display = 'none';
    }else if(cfg.tmdb_api_key === '***'){
      document.getElementById('tmdbKeyWarning').style.display = 'none';
    }else{
      document.getElementById('tmdbKeyWarning').style.display = 'flex';
      document.getElementById('tmdbGrid').innerHTML = '<div class="tmdb-empty">请先在设置中配置 TMDB API Key</div>';
      return;
    }
  }catch(e){}
  // 加载类型和列表
  await loadTmdbGenres();
  await loadTmdbList();
}

function updateTmdbListTypeOptions(listTypes){
  var sel = document.getElementById('tmdbListType');
  var current = sel.value || 'trending';
  sel.innerHTML = listTypes.map(function(l){return '<option value="'+l.code+'">'+l.name+'</option>'}).join('');
  // 尝试保持当前选择
  if([].some.call(sel.options, function(o){return o.value === current})){
    sel.value = current;
  }
}

async function loadTmdbGenres(){
  var mt = document.getElementById('tmdbMediaType').value;
  if(mt === tmdbState.media_type && tmdbState.genres.length) return;
  tmdbState.media_type = mt;
  try{
    var d = await apiGet('/api/tmdb/genres?media_type='+mt);
    tmdbState.genres = d.genres || [];
    var chips = document.getElementById('tmdbGenreChips');
    var html = '<span class="chip on" data-gid="0" onclick="toggleTmdbGenre(this)">全部</span>';
    html += tmdbState.genres.map(function(g){return '<span class="chip" data-gid="'+g.id+'" onclick="toggleTmdbGenre(this)">'+g.name+'</span>'}).join('');
    chips.innerHTML = html;
    tmdbState.genre_id = 0;
  }catch(e){
    console.error('TMDB genres error', e);
  }
}

function toggleTmdbGenre(el){
  // 如果点击的条目已选中，无需重新加载
  if(el.classList.contains('on')) return;
  var chips = document.querySelectorAll('#tmdbGenreChips .chip');
  chips.forEach(function(c){c.classList.remove('on')});
  el.classList.add('on');
  tmdbState.genre_id = parseInt(el.dataset.gid) || 0;
  tmdbState.page = 1;
  loadTmdbList();
}

function onTmdbFilterChange(){
  var mt = document.getElementById('tmdbMediaType').value;
  // 如果切换了 media_type，更新列表类型选项
  if(mt !== tmdbState.media_type){
    var listTypes = mt === 'movie' ? tmdbState.options.movie_list_types : tmdbState.options.tv_list_types;
    updateTmdbListTypeOptions(listTypes);
  }
  // 更新列表选项后重新获取 lt，确保使用最新的下拉框值
  var lt = document.getElementById('tmdbListType').value;
  // 如果用户手动从 discover 切换到其他榜单类型，清除 discover 专属筛选条件
  if(lt !== 'discover' && tmdbState.list_type === 'discover'){
    tmdbState.genre_id = 0;
    var chips = document.querySelectorAll('#tmdbGenreChips .chip');
    chips.forEach(function(c){c.classList.remove('on')});
    var allChip = document.querySelector('#tmdbGenreChips .chip[data-gid="0"]');
    if(allChip) allChip.classList.add('on');
    document.getElementById('tmdbMinRating').value = 0;
    document.getElementById('tmdbYear').value = '';
  }
  tmdbState.list_type = lt;
  // 如果不是 discover，禁用排序/评分/年份/类型筛选（仅 discover 有效）
  var isDiscover = lt === 'discover';
  document.getElementById('tmdbSort').disabled = !isDiscover;
  document.getElementById('tmdbMinRating').disabled = !isDiscover;
  document.getElementById('tmdbYear').disabled = !isDiscover;
  // 切换 media_type 时重新加载类型
  if(mt !== tmdbState.media_type){
    tmdbState.media_type = mt;
    tmdbState.genres = [];
    loadTmdbGenres().then(function(){tmdbState.page = 1; loadTmdbList()});
  }else{
    tmdbState.page = 1;
    loadTmdbList();
  }
}

async function loadTmdbList(){
  var mt = document.getElementById('tmdbMediaType').value;
  var lt = document.getElementById('tmdbListType').value;
  var region = document.getElementById('tmdbRegion').value;
  var sort = document.getElementById('tmdbSort').value;
  var minRating = parseFloat(document.getElementById('tmdbMinRating').value) || 0;
  var year = parseInt(document.getElementById('tmdbYear').value) || 0;
  var genreId = tmdbState.genre_id || 0;

  // 如果不是 discover，但用户选了类型/评分/年份，自动切换到 discover
  if(lt !== 'discover' && (genreId > 0 || minRating > 0 || year > 0)){
    document.getElementById('tmdbListType').value = 'discover';
    lt = 'discover';
    tmdbState.list_type = 'discover';
    onTmdbFilterChange();
    return;
  }

  var grid = document.getElementById('tmdbGrid');
  grid.innerHTML = '<div class="tmdb-loading" style="grid-column:1/-1"><div class="spinner"></div> 加载中...</div>';

  var params = 'media_type='+mt+'&list_type='+lt+'&page='+tmdbState.page;
  if(lt === 'discover'){
    if(genreId) params += '&genre_id='+genreId;
    if(year) params += '&year='+year;
    if(minRating) params += '&min_rating='+minRating;
    params += '&sort_by='+sort;
  }
  if(region) params += '&region='+region;

  try{
    var d = await apiGet('/api/tmdb/list?'+params);
    tmdbState.items = d.items || [];
    tmdbState.total_pages = d.total_pages || 1;
    renderTmdbGrid();
    renderTmdbPagination();
    if(d.error){
      grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">⚠ '+esc(d.error)+'</div>';
    }
  }catch(e){
    grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">加载失败: '+esc(e.message||'')+'<br><span style="font-size:12px;opacity:.7">请检查 TMDB API Key 是否正确，以及网络是否能访问 themoviedb.org</span></div>';
  }
}

function renderTmdbGrid(){
  var grid = document.getElementById('tmdbGrid');
  if(!tmdbState.items.length){
    grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">暂无数据</div>';
    return;
  }
  grid.innerHTML = tmdbState.items.map(function(item){
    var sel = tmdbState.selected[item.id] ? ' selected' : '';
    var poster = item.poster || '';
    var posterHtml = poster
      ? '<img class="tmdb-poster" src="'+esc(poster)+'" loading="lazy" alt="'+esc(item.title)+'">'
      : '<div class="tmdb-poster" style="display:flex;align-items:center;justify-content:center;font-size:40px;color:var(--text3)">🎬</div>';
    var ratingHtml = item.rating > 0
      ? '<div class="tmdb-rating-badge">★ '+item.rating.toFixed(1)+'</div>'
      : '';
    var yearStr = item.year ? item.year : '';
    var votesStr = item.votes > 0 ? (item.votes >= 1000 ? (item.votes/1000).toFixed(1)+'k' : item.votes) + '票' : '';
    var metaParts = [];
    if(yearStr) metaParts.push(yearStr);
    if(votesStr) metaParts.push(votesStr);
    return '<div class="tmdb-card'+sel+'" onclick="toggleTmdbCard(this,'+item.id+')">'+
      '<div class="tmdb-poster-wrap">'+posterHtml+ratingHtml+'</div>'+
      '<div class="tmdb-info">'+
        '<div class="tmdb-title">'+esc(item.title)+'</div>'+
        '<div class="tmdb-meta">'+metaParts.join(' · ')+'</div>'+
        (item.overview ? '<div class="tmdb-overview">'+esc(item.overview)+'</div>' : '')+
      '</div>'+
    '</div>';
  }).join('');
}

function toggleTmdbCard(el, id){
  if(tmdbState.selected[id]){
    delete tmdbState.selected[id];
    el.classList.remove('selected');
  }else{
    // 存储完整信息，以便翻页后仍能构建转存任务
    var item = null;
    for(var i=0;i<tmdbState.items.length;i++){
      if(tmdbState.items[i].id===id){item=tmdbState.items[i];break}
    }
    tmdbState.selected[id] = item ? {title: item.title} : {title: ''};
    el.classList.add('selected');
  }
  updateTmdbSelBar();
}

function updateTmdbSelBar(){
  var count = Object.keys(tmdbState.selected).length;
  document.getElementById('tmdbSelCount').textContent = '已选 '+count+' 部';
  document.getElementById('tmdbSelBar').classList.toggle('active', count > 0);
}

function clearTmdbSelection(){
  tmdbState.selected = {};
  document.querySelectorAll('.tmdb-card.selected').forEach(function(c){c.classList.remove('selected')});
  updateTmdbSelBar();
}

async function transferTmdbSelection(){
  var ids = Object.keys(tmdbState.selected);
  if(!ids.length){showToast('请先选择内容',false);return}
  // 构建转存任务 - 直接从 selected 中获取信息，不依赖当前页 items
  var mt = document.getElementById('tmdbMediaType').value;
  var category = mt === 'movie' ? 'movie' : 'tv';
  var savepath = '/批量转存/TMDB';
  var tasks = [];
  for(var i=0; i<ids.length; i++){
    var sel = tmdbState.selected[ids[i]];
    if(sel){
      tasks.push({
        path: 'tmdb',
        type: category,
        savepath: savepath,
        _wish: true,
        title: sel.title || ('TMDB_'+ids[i]),
        category: category
      });
    }
  }
  if(!tasks.length){showToast('未找到选中项',false);return}
  // 提前清空日志并切换 tab，避免 SSE 早期日志被竞态清除
  switchTab('manual');
  document.getElementById('logCard').style.display='block';
  logBefore=[]; document.getElementById('log').textContent='';
  addLog('[开始转存 TMDB 选中内容]');
  try{
    var d = await apiPost('/api/transfer',{tasks:tasks,limit:5,filters:{}});
    if(d.success){
      showToast('已提交 '+tasks.length+' 项转存',true);
      document.getElementById('stopBtn').style.display='inline-block';
      startLogPoll(3000);
      clearTmdbSelection();
    }else{
      if(d.conflict){
        showToast('已有转存任务在运行',false);
      }else{
        showToast(d.message||'转存失败',false);
      }
    }
  }catch(e){showToast('请求失败: '+e.message,false)}
}

function renderTmdbPagination(){
  var pg = document.getElementById('tmdbPagination');
  if(tmdbState.total_pages <= 1){
    pg.style.display = 'none';
    return;
  }
  pg.style.display = 'flex';
  document.getElementById('tmdbPageInfo').textContent = tmdbState.page + ' / ' + tmdbState.total_pages;
  document.getElementById('tmdbPrevBtn').disabled = tmdbState.page <= 1;
  document.getElementById('tmdbNextBtn').disabled = tmdbState.page >= tmdbState.total_pages;
}

function tmdbPrevPage(){
  if(tmdbState.page > 1){
    tmdbState.page--;
    loadTmdbList();
    window.scrollTo({top:0,behavior:'smooth'});
  }
}

function tmdbNextPage(){
  if(tmdbState.page < tmdbState.total_pages){
    tmdbState.page++;
    loadTmdbList();
    window.scrollTo({top:0,behavior:'smooth'});
  }
}

async function refreshTmdbCache(){
  try{
    var d = await apiGet('/api/tmdb/refresh');
    showToast(d.message||'已刷新',true);
    tmdbState.page = 1;
    await loadTmdbList();
  }catch(e){showToast('刷新失败',false)}
}

// ============ Search ============
var searchTimer=null;var searchAbort=null;
function toggleMobileSearch(){
  var w=document.querySelector('.search-wrapper');
  w.classList.toggle('mobile-show');
  if(w.classList.contains('mobile-show')){document.getElementById('searchInput').focus()}
}
function onSearchInput(){clearTimeout(searchTimer);var q=document.getElementById('searchInput').value.trim();
  var dropdown=document.getElementById('searchDropdown');
  var searchBox=document.getElementById('searchBox');
  searchBox.classList.toggle('has-value',!!q);
  if(!q){dropdown.classList.remove('show');document.getElementById('searchResults').innerHTML='';return}
  dropdown.classList.add('show');
  searchTimer=setTimeout(doSearch,500)}
function clearSearch(){
  var input=document.getElementById('searchInput');
  var searchBox=document.getElementById('searchBox');
  input.value='';
  searchBox.classList.remove('has-value');
  document.getElementById('searchDropdown').classList.remove('show');
  document.getElementById('searchResults').innerHTML='';
  input.focus();
}
async function doSearch(){
  var q=document.getElementById('searchInput').value.trim();
  if(!q)return;
  var dropdown=document.getElementById('searchDropdown');
  dropdown.classList.add('show');
  var el=document.getElementById('searchResults');
  el.textContent='';
  var skWrap = document.createElement('div');
  skWrap.style.cssText = 'text-align:center;padding:20px;color:var(--text3)';
  var sk1 = document.createElement('div');
  sk1.className = 'skeleton-text';
  sk1.style.cssText = 'width:60%;margin:0 auto 8px';
  var sk2 = document.createElement('div');
  sk2.className = 'skeleton-text';
  sk2.style.cssText = 'width:40%;margin:0 auto';
  skWrap.appendChild(sk1);
  skWrap.appendChild(sk2);
  el.appendChild(skWrap);
  if(searchAbort)searchAbort.abort();
  searchAbort=new AbortController();
  try{var d=await apiGet('/api/search?q='+encodeURIComponent(q),2,searchAbort.signal);
    if(!d.results||!d.results.length){
      el.textContent='';
      var emptyWrap = document.createElement('div');
      emptyWrap.className = 'empty-state';
      emptyWrap.style.padding = '28px 20px';
      var emptyIcon = document.createElement('div');
      emptyIcon.className = 'empty-icon';
      emptyIcon.style.cssText = 'width:44px;height:44px';
      emptyIcon.innerHTML = '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><use href="#icon-search"/></svg>';
      var emptyTitle = document.createElement('div');
      emptyTitle.className = 'empty-title';
      emptyTitle.textContent = '没有找到相关资源';
      var emptyDesc = document.createElement('div');
      emptyDesc.className = 'empty-desc';
      emptyDesc.innerHTML = '试试换个关键词，或检查拼写<br>也可以去「手动转存」从榜单中选择';
      emptyWrap.appendChild(emptyIcon);
      emptyWrap.appendChild(emptyTitle);
      emptyWrap.appendChild(emptyDesc);
      el.appendChild(emptyWrap);
      return;
    }
    el.textContent='';
    d.results.forEach(function(r){
      var item = document.createElement('div');
      item.className = 'search-item';
      var infoDiv = document.createElement('div');
      infoDiv.style.cssText = 'flex:1;min-width:0';
      var titleDiv = document.createElement('div');
      titleDiv.className = 'search-item-title';
      titleDiv.textContent = r.title;
      var sourceDiv = document.createElement('div');
      sourceDiv.className = 'search-item-source';
      sourceDiv.textContent = r.source||'夸克网盘';
      infoDiv.appendChild(titleDiv);
      infoDiv.appendChild(sourceDiv);
      var btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-primary';
      btn.textContent = '转存';
      btn.dataset.title = r.title;
      btn.dataset.url = r.url;
      btn.addEventListener('click', function(){ transferOne(this); });
      item.appendChild(infoDiv);
      item.appendChild(btn);
      el.appendChild(item);
    });}
  catch(e){
    if(e.name==='AbortError')return;
    el.textContent='';
    var failWrap = document.createElement('div');
    failWrap.className = 'empty-state';
    failWrap.style.padding = '24px';
    var failIcon = document.createElement('div');
    failIcon.className = 'empty-icon';
    failIcon.style.cssText = 'width:40px;height:40px;color:var(--red)';
    failIcon.innerHTML = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-x-circle"/></svg>';
    var failTitle = document.createElement('div');
    failTitle.className = 'empty-title';
    failTitle.textContent = '搜索失败';
    var failDesc = document.createElement('div');
    failDesc.className = 'empty-desc';
    failDesc.textContent = e.message||'网络错误';
    failWrap.appendChild(failIcon);
    failWrap.appendChild(failTitle);
    failWrap.appendChild(failDesc);
    el.appendChild(failWrap);
  }}
document.addEventListener('click',function(e){
  var dropdown=document.getElementById('searchDropdown');
  var wrapper=document.querySelector('.search-wrapper');
  if(dropdown.classList.contains('show')&&!wrapper.contains(e.target)){
    dropdown.classList.remove('show');
  }
});

async function transferOne(btn){
  var title = btn.dataset.title;
  var url = btn.dataset.url;
  var savepath = localStorage.getItem('search_path')||'/批量转存/手动搜索存';
  btn.disabled=true; btn.textContent='转存中...';
  try{
    var r = await apiPost('/api/transfer_one',{title:title, shareurl:url, savepath:savepath});
    if(r.success){showToast('已提交转存: '+title,true); btn.textContent='已转存'; btn.className='btn btn-sm btn-outline'}
    else{showToast(r.message||'失败',false); btn.disabled=false; btn.textContent='转存'}
  }catch(e){showToast('请求失败',false); btn.disabled=false; btn.textContent='转存'}
}

// ============ Dashboard ============
async function loadDashboard(){
  try{
    var s = await apiGet('/api/schedule');
    if(s._status){
      var ss=s._status;
      var dashNextEl = document.getElementById('dashNext');
      dashNextEl.textContent = '';
      var gridDiv = document.createElement('div');
      gridDiv.style.cssText = 'display:grid;grid-template-columns:auto 1fr;gap:3px 10px;align-items:center';
      if(ss.transfer_next){
        var tLabel = document.createElement('span');
        tLabel.style.cssText = 'display:flex;align-items:center;gap:5px;white-space:nowrap;color:var(--text3);font-size:12px';
        tLabel.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-transfer"/></svg> 转存';
        var tVal = document.createElement('span');
        tVal.style.cssText = 'text-align:right;color:var(--text2);white-space:nowrap;font-size:12px;font-weight:500';
        tVal.textContent = ss.transfer_next.slice(5);
        gridDiv.appendChild(tLabel);
        gridDiv.appendChild(tVal);
      }
      if(ss.expired_check_next){
        var eLabel = document.createElement('span');
        eLabel.style.cssText = 'display:flex;align-items:center;gap:5px;white-space:nowrap;color:var(--text3);font-size:12px';
        eLabel.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-refresh"/></svg> 检测';
        var eVal = document.createElement('span');
        eVal.style.cssText = 'text-align:right;color:var(--text2);white-space:nowrap;font-size:12px;font-weight:500';
        eVal.textContent = ss.expired_check_next.slice(5);
        gridDiv.appendChild(eLabel);
        gridDiv.appendChild(eVal);
      }
      if(ss.transfer_next || ss.expired_check_next){
        dashNextEl.appendChild(gridDiv);
      } else {
        dashNextEl.textContent = '暂无调度';
      }
    }
    var stats = await apiGet('/api/dashboard/stats');
    var todayEl = document.getElementById('dashToday');
    if(stats.today_count){animateNumber(todayEl,stats.today_count,800)}else{todayEl.textContent='-'}
    animateNumber(document.getElementById('dash7OK'),stats.week_ok||0,900);
    animateNumber(document.getElementById('dash7Fail'),stats.week_fail||0,900);
    animateNumber(document.getElementById('dash7Total'),stats.week_total||0,1000);
    var dotEl = document.getElementById('dashStatusDot');
    var textEl = document.getElementById('dashStatusText');
    var timeEl = document.getElementById('dashLastTime');
    if(stats.last_status && stats.last_status !== 'none'){
      var statusMap={
        'success':{text:'成功',dotClass:'status-dot',textColor:'color:var(--green)'},
        'partial':{text:'部分成功',dotClass:'status-dot orange',textColor:'color:var(--orange)'},
        'fail':{text:'失败',dotClass:'status-dot red',textColor:'color:var(--red)'},
        'none':{text:'无新增',dotClass:'',textColor:''}
      };
      var st=statusMap[stats.last_status]||statusMap['none'];
      dotEl.className = st.dotClass;
      dotEl.style.display = st.dotClass ? 'inline-block' : 'none';
      textEl.textContent = st.text;
      textEl.style.cssText = st.textColor + ';font-size:16px;font-weight:600;font-family:var(--font-display)';
      timeEl.textContent=stats.last_time?stats.last_time.slice(5,16):'上次转存';
    }else{
      dotEl.style.display='none';
      textEl.textContent='-';
      textEl.style.cssText='color:var(--text);font-size:16px;font-weight:600';
      timeEl.textContent='上次转存';
    }
    try{var v=await apiGet('/version');if(v.version)document.getElementById('headerVersion').textContent='v'+v.version}catch(e){}
  }catch(e){}
}

// ============ Init ============
function initTimeSelects(){
  var hours=['schedTHour','schedEHour'];
  var mins=['schedTMin','schedEMin'];
  for(var h=0;h<hours.length;h++){
    var sel=document.getElementById(hours[h]);
    if(sel)for(var i=0;i<24;i++){var o=document.createElement('option');o.value=i;o.textContent=String(i).padStart(2,'0');sel.appendChild(o)}
  }
  for(var m=0;m<mins.length;m++){
    var sel2=document.getElementById(mins[m]);
    if(sel2)for(var i=0;i<60;i+=5){var o2=document.createElement('option');o2.value=i;o2.textContent=String(i).padStart(2,'0');sel2.appendChild(o2)}
  }
}

async function init(){
  initTheme(); initTimeSelects();
  try{C = await apiGet('/api/categories')}
  catch(e){showToast('认证失败，请重新登录',false);setTimeout(function(){location.href='/login.html'},1500);return}
  parseCategories(); parseSchedCats(); loadSchedule(); loadExecHistory();
  checkRunningStatus(); loadDashboard();
  updateTabIndicator();
  window.addEventListener('resize', updateTabIndicator);
  initSSE();
}

var sseRetryDelay=5000;
function initSSE(){
  if(!window.EventSource) return;
  var es = new EventSource('/api/sse');
  es.onopen=function(){
    sseConnected=true;
    sseRetryDelay=5000;
    if(logPollTimer&&logPollInterval===3000){
      clearInterval(logPollTimer);startLogPoll(10000);
    }
  };
  es.addEventListener('schedule_update', function(){
    loadDashboard();
  });
  es.addEventListener('config_update', function(){
    loadConfig();
  });
  es.addEventListener('history_update', function(){
    loadExecHistory();
  });
  es.addEventListener('transfer_progress', function(e){
    var d=JSON.parse(e.data);
    if(d.stats){
      document.getElementById('stSearched').textContent=d.stats.searched||0;
      document.getElementById('stOK').textContent=d.stats.ok||0;
      document.getElementById('stSkip').textContent=d.stats.skipped||0;
      document.getElementById('stFail').textContent=d.stats.failed||0;
      document.getElementById('stTotal').textContent=d.stats.total||0;
      updateProgress(d.stats);
    }
    if(d.running)document.getElementById('stopBtn').style.display='inline-block';
    else document.getElementById('stopBtn').style.display='none';
  });
  es.addEventListener('log', function(e){
    var d=JSON.parse(e.data);
    if(d.line)addLog(d.line);
  });
  es.onerror = function(){
    es.close();
    sseConnected=false;
    if(logPollTimer&&logPollInterval===10000){
      // SSE 断连，切换到 3s 轮询；首次仅对齐游标，不重复添加已有日志
      clearInterval(logPollTimer);startLogPoll(3000, 0, true);
    }
    setTimeout(initSSE, sseRetryDelay);
    sseRetryDelay=Math.min(sseRetryDelay*2, 30000);
  };
}
init();
