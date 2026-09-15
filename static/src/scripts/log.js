// ============ Log ============

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
  updateLogHint();
}
// 超长日志行折行后需要缩进，否则续行顶到行首、时间戳与正文错位，
// 视觉上像「内容被截断」。折行缩进在 CSS 里通过 --log-wrap-pad 生效，
// 这里实测首行行盒宽度与容器宽度的比值来判定是否需要缩进。
function markWrap(lineEl){
  var el = document.getElementById('log');
  if(!el) return;
  var pad = getComputedStyle(el).paddingLeft ? parseFloat(getComputedStyle(el).paddingLeft) : 0;
  var avail = el.clientWidth - pad * 2;
  if(avail <= 0) return;
  // 用单行渲染测宽：整数宽度即可，避免引入 canvas 测量成本
  var probe = document.createElement('span');
  probe.className = lineEl.className;
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;left:-9999px';
  probe.textContent = lineEl.textContent;
  document.body.appendChild(probe);
  var need = probe.getBoundingClientRect().width > avail;
  document.body.removeChild(probe);
  if(need) lineEl.classList.add('wrapped');
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
  markWrap(lineEl);
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
  var logEl = document.getElementById('log');
  if(logEl && !logPaused) logEl.scrollTop = logEl.scrollHeight; // 恢复滚动时回到最新
  updateLogHint();
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

// 日志搜索功能（OPT-06：输入去抖，避免每次按键都全量重渲染日志）
var logSearchTimer = null;
function filterLogSearch(keyword){
  if(logSearchTimer) clearTimeout(logSearchTimer);
  var kw = (keyword||'').toLowerCase();
  logSearchTimer = setTimeout(function(){ applyLogSearch(kw); }, 200);
}
function applyLogSearch(kw){
  var el = document.getElementById('log');
  el.textContent='';
  for(var i=0;i<logBefore.length;i++){
    var line = logBefore[i];
    if(kw && line.toLowerCase().indexOf(kw)<0) continue;
    if(matchFilter(line)) appendLine(line);
  }
  // 搜索结果同样定位到最新，避免翻到顶部后误以为「日志不全」
  if(!logPaused) el.scrollTop = el.scrollHeight;
  updateLogHint();
}

// 日志区底部提示：明确「当前看到的是最新」还是「已暂停/翻到了历史」。
// 旧版更新时会把 scrollTop 置 0（停在最旧一行），且没有任何提示，
// 用户会以为日志"显示不全"（issue #11）。
function updateLogHint(){
  var el = document.getElementById('log');
  var hint = document.getElementById('logHint');
  if(!el || !hint) return;
  var overflowing = el.scrollHeight > el.clientHeight + 1;
  if(!overflowing){ hint.textContent = '共 ' + logBefore.length + ' 条'; return; }
  var atTop = el.scrollTop <= 4;
  var atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
  if(logPaused && atTop) hint.textContent = '已暂停 · 显示最早 ' + logBefore.length + ' 条';
  else if(logPaused) hint.textContent = '已暂停滚动 · 共 ' + logBefore.length + ' 条';
  else if(atBottom) hint.textContent = '最新 ' + logBefore.length + ' 条 · 已定位到末尾';
  else hint.textContent = '历史记录 · 共 ' + logBefore.length + ' 条';
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
        document.getElementById('stopBtn').style.display='none';addLog('全部完成');srAnnounce('转存全部完成');
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
  // 增强进度区域
  var progWrap=document.getElementById('transferProgressWrap');
  var progCount=document.getElementById('progressCount');
  var progEta=document.getElementById('progressEta');
  var progCurrent=document.getElementById('progressCurrent');
  if(progWrap) progWrap.style.display = total>0 ? 'block' : 'none';
  if(ringFill){
    var circumference=2*Math.PI*18;
    var offset=circumference-(pct/100)*circumference;
    ringFill.style.strokeDasharray=circumference;
    ringFill.style.strokeDashoffset=offset;
  }
  if(ringText)ringText.textContent=pct+'%';
  if(ringSub){
    if(total>0){
      var elapsed = 0, elapsedStr = '', etaStr = '';
      if(stats.start_time){
        try{
          var start = new Date(stats.start_time.replace(' ','T'));
          var now = new Date();
          elapsed = Math.floor((now-start)/1000);
          var mins = Math.floor(elapsed/60);
          var secs = elapsed%60;
          elapsedStr = ' | 已用 '+mins+'分'+secs+'秒';
          // 预估剩余时间
          if(done>0 && done<total){
            var avgPerItem = elapsed/done;
            var remaining = Math.round(avgPerItem*(total-done));
            var remMins = Math.floor(remaining/60);
            var remSecs = remaining%60;
            etaStr = ' · 预计剩余 '+remMins+'分'+remSecs+'秒';
          }
        }catch(e){}
      }
      ringSub.textContent='已完成 '+done+' / '+total+' 个任务'+elapsedStr;
      if(progCount) progCount.textContent = done+' / '+total+' · 成功'+(stats.ok||0)+' 失败'+(stats.failed||0)+' 跳过'+(stats.skipped||0);
      if(progEta) progEta.textContent = etaStr;
      // 从最近日志提取当前处理条目
      if(progCurrent && logBefore.length>0){
        var lastLine = logBefore[logBefore.length-1] || '';
        // 尝试提取正在处理的标题
        var match = lastLine.match(/[《【]([^》】]+)[》】]/) || lastLine.match(/转存[：:]\s*(.+)/);
        if(match && match[1]){
          progCurrent.textContent = '当前：' + match[1].slice(0,40);
        } else if(lastLine.indexOf('搜索')>=0 || lastLine.indexOf('转存')>=0){
          progCurrent.textContent = lastLine.slice(0,50);
        }
      }
    }else{
      ringSub.textContent='正在搜索资源...';
      if(progCount) progCount.textContent='搜索中...';
      if(progEta) progEta.textContent='';
      if(progCurrent) progCurrent.textContent='';
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
      addLog('发现 '+d.expired.length+' 个失效链接');srAnnounce('发现 '+d.expired.length+' 个失效链接');
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
          addLog('启动修复失败: '+(fd.message||'未知错误'));srAnnounce('修复启动失败：'+(fd.message||'未知错误'));
        }
      }
    }else{addLog('所有链接正常');srAnnounce('所有链接正常');}
  }catch(e){addLog('检测失败: '+e.message)}
}


// ============ 日志面板折叠/展开 ============
// 折叠态同步：更新展开按钮的 aria-expanded / 标题，并在桌面态显示 FAB 作为备用入口。
function _syncLogPanelA11y(){
  var panel = document.getElementById('logPanel');
  if(!panel) return;
  var collapsed = panel.classList.contains('collapsed');
  var btn = document.getElementById('logToggleBtn');
  if(btn){
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    btn.setAttribute('aria-label', collapsed ? '展开执行日志' : '收起执行日志');
    btn.title = collapsed ? '展开执行日志' : '收起执行日志';
  }
  // 桌面态折叠后网格轨道只剩 48px，FAB 提供还原入口（窄屏 FAB 由媒体查询控制）
  var fab = document.getElementById('logFab');
  if(fab && !isNarrowViewport()) fab.style.display = collapsed ? 'flex' : 'none';
}
function toggleLogPanel(){
  var panel = document.getElementById('logPanel');
  if(!panel) return;
  panel.classList.toggle('collapsed');
  try{ localStorage.setItem('logPanelCollapsed', panel.classList.contains('collapsed') ? '1' : '0'); }catch(e){}
  _syncLogPanelA11y();
  if(typeof tmdbUpdateBackToTop==='function') tmdbUpdateBackToTop(); // OPT-28：面板态变化时刷新回顶按钮避让
}
function collapseLogPanel(){
  var panel = document.getElementById('logPanel');
  if(panel && !panel.classList.contains('collapsed')) panel.classList.add('collapsed');
  _syncLogPanelA11y();
  if(typeof tmdbUpdateBackToTop==='function') tmdbUpdateBackToTop();
}
function expandLogPanel(){
  var panel = document.getElementById('logPanel');
  if(!panel) return;
  // 窄屏/移动端：日志栏是「覆盖层抽屉」，仅去掉 .collapsed 仍是 translateX(100%)
  // 的屏幕外状态；必须同时打开 .open，否则展开动作看起来无效。
  if(isNarrowViewport()) panel.classList.add('open');
  if(panel.classList.contains('collapsed')) panel.classList.remove('collapsed');
  _syncLogPanelA11y();
  if(typeof tmdbUpdateBackToTop==='function') tmdbUpdateBackToTop();
  // 展开后定位到最新一条：否则长日志停在最早一行，仍像"显示不全"
  positionLogDrawerAtLatest();
}
// 把日志区滚到最新一条并刷新底部提示。
// 抽屉刚从屏幕外归位时（display/高度在这一帧才确定）立即读 scrollHeight 会拿到
// 旧值，因此再补一次 next-frame 定位；两处都做是为了宽屏展开也保持即时。
function positionLogDrawerAtLatest(){
  var el = document.getElementById('log');
  if(!el) return;
  if(!logPaused) el.scrollTop = el.scrollHeight;
  updateLogHint();
  requestAnimationFrame(function(){
    if(logPaused) return;
    el.scrollTop = el.scrollHeight;
    updateLogHint();
  });
}
// 恢复折叠状态；桌面态若恢复为折叠则同步显示展开入口
(function(){
  try{
    document.addEventListener('DOMContentLoaded', function(){
      restoreLogPanelState();
      // 首屏再归一化一次：restoreLogPanelState 之后视口可能仍是窄屏，
      // 此时任何遗留的折叠态都会让日志栏不可见（issue #11）。
      if(typeof normalizeLogPanelForViewport === 'function') normalizeLogPanelForViewport();
      var el = document.getElementById('log');
      if(el) el.addEventListener('scroll', updateLogHint, {passive:true});
      // 窗口尺寸变化后折行判定会变，重新标注一次
      var rt = null;
      window.addEventListener('resize', function(){
        // 宽窄视口切换要立即归一化：宽→窄若残留 .collapsed，日志栏会停在
        // 屏幕外/48px 轨道（issue #11 现场），这里不等防抖、先修状态。
        if(typeof normalizeLogPanelForViewport === 'function') normalizeLogPanelForViewport();
        if(rt) clearTimeout(rt);
        rt = setTimeout(function(){
          document.querySelectorAll('#log .log-line.wrapped').forEach(function(n){ n.classList.remove('wrapped'); markWrap(n); });
          var e2 = document.getElementById('log');
          if(e2 && !logPaused) e2.scrollTop = e2.scrollHeight;
          updateLogHint();
        }, 150);
      });
    });
  }catch(e){}
})();
