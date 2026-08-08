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

// ---- 紧凑表格渲染 + 排序 ----
var execHistorySort = {key:'time', dir:'desc'};
function statusClass(st){
  if(st==='ok'||st==='done') return 'ok';
  if(st==='fail'||st==='error') return 'fail';
  if(st==='partial') return 'partial';
  return 'none';
}
function typeLabel(t){
  if(t==='transfer') return '转存';
  if(t==='expired_check') return '检测';
  if(t==='config') return '配置';
  return t||'';
}
function resultScore(h){
  var ok=(h.data&&h.data.ok)||0, fail=(h.data&&h.data.failed)||0, skip=(h.data&&h.data.skipped)||0;
  return ok+fail+skip;
}
function resultBadges(h){
  if(h.type==='transfer'){
    var ok=(h.data&&h.data.ok)||0, fail=(h.data&&h.data.failed)||0, skip=(h.data&&h.data.skipped)||0;
    return '<span class="badge-ok">成功 '+ok+'</span> <span class="badge-fail">失败 '+fail+'</span> <span class="badge-skip">跳过 '+skip+'</span>';
  }
  if(h.type==='expired_check'){
    var n=(h.data&&h.data.expired)?h.data.expired.length:0;
    return n>0 ? '<span class="badge-fail">失效 '+n+'</span>' : '<span class="badge-ok">全部正常</span>';
  }
  return '<span style="color:var(--text3)">—</span>';
}
function getSortedFiltered(){
  var filtered = execHistoryFilter==='all'
    ? execHistoryData.filter(function(h){return h.type!=='history'&&h.type!=='schedule'})
    : execHistoryData.filter(function(h){return h.type===execHistoryFilter});
  var k=execHistorySort.key, dir=execHistorySort.dir==='asc'?1:-1;
  var order={ok:3,partial:2,fail:1,none:0};
  filtered = filtered.slice().sort(function(a,b){
    var primary=0;
    if(k==='time') primary=(a.time||'').localeCompare(b.time||'');
    else if(k==='result') primary=resultScore(a)-resultScore(b);
    else if(k==='status') primary=(order[statusClass(a.status)]||0)-(order[statusClass(b.status)]||0);
    if(primary!==0) return primary*dir;
    return (b.time||'').localeCompare(a.time||''); // 同序时最新在前
  });
  return filtered;
}
function toggleSort(key){
  if(execHistorySort.key===key) execHistorySort.dir = execHistorySort.dir==='asc'?'desc':'asc';
  else { execHistorySort.key=key; execHistorySort.dir='desc'; }
  renderExecHistory();
}
function renderExecHistory(){
  var el = document.getElementById('execHistoryList');
  el.textContent='';
  var data = getSortedFiltered();
  if(!data.length){
    el.appendChild(_makeEmptyState('icon-clock','还没有执行记录','去「手动转存」选择一个榜单开始吧<br>每次转存和检测都会记录在这里'));
    return;
  }
  var table = document.createElement('table');
  table.className = 'data-table';
  table.id = 'execHistoryTable';
  var thead = document.createElement('thead');
  var htr = document.createElement('tr');
  var cols = [{k:null,t:'状态'},{k:null,t:'类型'},{k:null,t:'详情'},{k:'result',t:'成功/失败/跳过'},{k:'time',t:'时间'}];
  cols.forEach(function(c){
    var th = document.createElement('th');
    th.textContent = c.t;
    if(c.k){
      th.setAttribute('data-sort', c.k);
      if(execHistorySort.key===c.k){
        th.setAttribute('aria-sort', execHistorySort.dir==='asc'?'ascending':'descending');
      } else {
        th.setAttribute('aria-sort', 'none');
      }
      var ind = document.createElement('span'); ind.className='sort-ind';
      if(execHistorySort.key===c.k) ind.textContent = execHistorySort.dir==='asc'?'▲':'▼';
      th.appendChild(ind);
      th.addEventListener('click', function(){ toggleSort(c.k); });
    }
    htr.appendChild(th);
  });
  thead.appendChild(htr); table.appendChild(thead);
  var tbody = document.createElement('tbody');
  data.forEach(function(h){
    var hid = String(h.id);
    var tr = document.createElement('tr');
    tr.className = 'hist-row'; tr.setAttribute('data-id', hid);
    var td1 = document.createElement('td');
    td1.innerHTML = '<span class="status-dot-cell '+statusClass(h.status)+'" title="'+esc(h.status||'')+'"></span>';
    var td2 = document.createElement('td'); td2.className='hist-type'; td2.textContent = typeLabel(h.type);
    var td3 = document.createElement('td'); td3.className='hist-detail-cell'; td3.textContent = h.detail||'';
    var td4 = document.createElement('td'); td4.innerHTML = resultBadges(h);
    var td5 = document.createElement('td'); td5.className='hist-time'; td5.textContent = h.time||'';
    [td1,td2,td3,td4,td5].forEach(function(td){ tr.appendChild(td); });
    tr.addEventListener('click', function(){ toggleHistDetail(hid); });
    tbody.appendChild(tr);
    if(h.data && (h.data.results || h.data.expired)){
      var dtr = document.createElement('tr');
      dtr.className = 'hist-detail-row'; dtr.id = 'hist_detail_'+hid; dtr.style.display='none';
      var dtd = document.createElement('td'); dtd.colSpan = 5; dtd.innerHTML = renderHistDetailContent(h);
      dtr.appendChild(dtd); tbody.appendChild(dtr);
    }
  });
  table.appendChild(tbody); el.appendChild(table);
  if(histMore){
    var moreDiv = document.createElement('div');
    moreDiv.style.cssText = 'text-align:center;padding:12px;margin-top:6px';
    var moreBtn = document.createElement('button');
    moreBtn.className = 'btn btn-outline btn-sm'; moreBtn.textContent = '加载更多';
    moreBtn.addEventListener('click', loadMoreHistory);
    moreDiv.appendChild(moreBtn); el.appendChild(moreDiv);
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
  detailEl.style.display = (detailEl.style.display === 'none' || detailEl.style.display === '') ? 'table-row' : 'none';
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

