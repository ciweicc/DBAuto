// ============ Dashboard ============
async function loadDashboard(){
  try{
    var d = await apiGet('/api/dashboard/all');
    var s = d.schedule_status || {};
    var dashNextEl = document.getElementById('dashNext');
    dashNextEl.textContent = '';
    var gridDiv = document.createElement('div');
    gridDiv.style.cssText = 'display:grid;grid-template-columns:auto 1fr;gap:3px 10px;align-items:center';
    if(s.transfer_next){
      var tLabel = document.createElement('span');
      tLabel.style.cssText = 'display:flex;align-items:center;gap:5px;white-space:nowrap;color:var(--text3);font-size:12px';
      tLabel.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-transfer"/></svg> 转存';
      var tVal = document.createElement('span');
      tVal.style.cssText = 'text-align:right;color:var(--text2);white-space:nowrap;font-size:12px;font-weight:500';
      tVal.textContent = s.transfer_next.slice(5);
      gridDiv.appendChild(tLabel);
      gridDiv.appendChild(tVal);
    }
    if(s.expired_check_next){
      var eLabel = document.createElement('span');
      eLabel.style.cssText = 'display:flex;align-items:center;gap:5px;white-space:nowrap;color:var(--text3);font-size:12px';
      eLabel.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-refresh"/></svg> 检测';
      var eVal = document.createElement('span');
      eVal.style.cssText = 'text-align:right;color:var(--text2);white-space:nowrap;font-size:12px;font-weight:500';
      eVal.textContent = s.expired_check_next.slice(5);
      gridDiv.appendChild(eLabel);
      gridDiv.appendChild(eVal);
    }
    if(s.transfer_next || s.expired_check_next){
      dashNextEl.appendChild(gridDiv);
    } else {
      dashNextEl.textContent = '暂无调度';
    }
    var stats = d.stats || {};
    var todayEl = document.getElementById('dashToday');
    if(stats.today_count){animateNumber(todayEl,stats.today_count,800)}else{todayEl.textContent='-'}
    animateNumber(document.getElementById('dash7OK'),stats.week_ok||0,900);
    animateNumber(document.getElementById('dash7Fail'),stats.week_fail||0,900);
    animateNumber(document.getElementById('dash7Total'),stats.week_total||0,1000);
    renderDash7Viz(stats);
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
    if(d.version) document.getElementById('headerVersion').textContent='v'+d.version;
  }catch(e){}
  finally{
    // 骨架屏 → 真实内容
    var sk = document.getElementById('dashSkeleton');
    var ct = document.getElementById('dashContent');
    if(sk) sk.style.display = 'none';
    if(ct) ct.style.display = 'grid';
  }
}

// 近 7 天柱状图 + 成功率环形图
function renderDash7Viz(stats){
  var chartEl = document.getElementById('dash7Chart');
  if(chartEl && stats.daily && stats.daily.length){
    chartEl.textContent='';
    var maxTotal = 1;
    for(var i=0;i<stats.daily.length;i++){ if(stats.daily[i].total>maxTotal) maxTotal=stats.daily[i].total; }
    for(var j=0;j<stats.daily.length;j++){
      var d = stats.daily[j];
      var bar = document.createElement('div');
      bar.className='dash7-bar';
      var h = Math.round(d.total/maxTotal*100);
      bar.style.height = (h<5?5:h) + '%';
      bar.title = d.date + '　成功 '+d.ok+' / 失败 '+d.fail;
      if(d.ok===0 && d.fail>0) bar.style.background='var(--red)';
      else if(d.fail>0) bar.style.background='linear-gradient(180deg,var(--green),var(--orange))';
      else if(d.ok>0) bar.style.background='var(--green)';
      else bar.style.background='var(--text4)';
      chartEl.appendChild(bar);
    }
  }
  var rate = (stats.week_total||0)>0 ? Math.round((stats.week_ok||0)/stats.week_total*100) : 0;
  var donut = document.getElementById('dash7Donut');
  if(donut){
    var circ = 2*Math.PI*18;
    donut.style.strokeDasharray = circ;
    donut.style.strokeDashoffset = circ*(1-rate/100);
  }
  var rateEl = document.getElementById('dash7Rate');
  if(rateEl) rateEl.textContent = rate + '%';
}

