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
  playSound('click');
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

