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
