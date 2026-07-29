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
  var savepath = APP_PATHS.search||'/批量转存/手动搜索存';
  btn.disabled=true; btn.textContent='转存中...';
  try{
    var r = await apiPost('/api/transfer_one',{title:title, shareurl:url, savepath:savepath});
    if(r.success){showToast('已提交转存: '+title,true); btn.textContent='已转存'; btn.className='btn btn-sm btn-outline'}
    else{showToast(r.message||'失败',false); btn.disabled=false; btn.textContent='转存'}
  }catch(e){showToast('请求失败',false); btn.disabled=false; btn.textContent='转存'}
}

