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

