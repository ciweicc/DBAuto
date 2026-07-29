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

// 智能滚动跟随：用户手动上翻时暂停自动滚动，回到底部时恢复
(function(){
  var logBox = document.getElementById('log');
  if(logBox){
    logBox.addEventListener('scroll', function(){
      var isAtBottom = this.scrollHeight - this.scrollTop <= this.clientHeight + 50;
      if(!logPaused && !isAtBottom){
        // 用户上翻，临时暂停（不改变 logPaused 状态，只是不滚动到底部）
      }
    });
  }
})();

// 日志搜索功能
function filterLogSearch(keyword){
  var el = document.getElementById('log');
  el.textContent='';
  var kw = (keyword||'').toLowerCase();
  for(var i=0;i<logBefore.length;i++){
    var line = logBefore[i];
    if(kw && line.toLowerCase().indexOf(kw)<0) continue;
    if(matchFilter(line)) appendLine(line);
  }
}

// 复制全部日志
function copyAllLogs(){
  var text = logBefore.join('\n');
  if(navigator.clipboard){
    navigator.clipboard.writeText(text).then(function(){
      showToast('已复制 '+logBefore.length+' 条日志',true);
    }).catch(function(){
      showToast('复制失败',false);
    });
  }else{
    // 降级方案
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try{document.execCommand('copy');showToast('已复制 '+logBefore.length+' 条日志',true)}
    catch(e){showToast('复制失败',false)}
    document.body.removeChild(ta);
  }
}

function startLogPoll(interval, initLen, skipFirstSync){
  if(logPollTimer)clearInterval(logPollTimer);
  var pollInterval=interval||3000;
  var lastProgressLen=initLen||0;
  var firstSyncSkip=skipFirstSync||false;
  logPollInterval=pollInterval;
  logPollTimer = setInterval(async function(){
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
        document.getElementById('stopBtn').style.display='none';addLog('全部完成');
        if(d.stats&&(d.stats.failed||0)>0)playSound('error');else playSound('success');}
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
      var elapsed = '';
      if(stats.start_time){
        try{
          var start = new Date(stats.start_time.replace(' ','T'));
          var now = new Date();
          var diff = Math.floor((now-start)/1000);
          var mins = Math.floor(diff/60);
          var secs = diff%60;
          elapsed = ' | 已用 '+mins+'分'+secs+'秒';
        }catch(e){}
      }
      ringSub.textContent='已完成 '+done+' / '+total+' 个任务'+elapsed;
    }else{
      ringSub.textContent='正在搜索资源...';
    }
  }
}

async function checkRunningStatus(){
  try{
    var d = await apiGet('/api/transfer/status');
    if(d.running){
      document.getElementById('stopBtn').style.display='inline-block';
      var initLen=0;
      if(d.progress&&d.progress.length>0){logBefore=[];for(var i=0;i<d.progress.length;i++)addLog(d.progress[i]);initLen=d.progress.length}
      startLogPoll(3000, initLen);
    }
  }catch(e){showToast('检查运行状态失败',false)}
}
async function checkExpired(){
  if(logPollTimer){clearInterval(logPollTimer);logPollTimer=null;logPollInterval=0}
  logBefore=[]; document.getElementById('log').textContent='';
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

