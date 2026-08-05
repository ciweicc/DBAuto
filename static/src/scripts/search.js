// ============ Search ============
var searchTimer=null;var searchAbort=null;var searchCheckSeq=0;
// 键盘导航状态：当前高亮的结果项索引 + 结果项 DOM 引用
var searchActiveIndex=-1;
var searchOptions=[];

function toggleMobileSearch(){
  var w=document.querySelector('.search-wrapper');
  w.classList.toggle('mobile-show');
  if(w.classList.contains('mobile-show')){document.getElementById('searchInput').focus()}
}

// 关闭下拉并重置键盘导航状态 / ARIA（无障碍）
function closeSearch(){
  var dropdown=document.getElementById('searchDropdown');
  var input=document.getElementById('searchInput');
  if(dropdown) dropdown.classList.remove('show');
  if(input){
    input.setAttribute('aria-expanded','false');
    input.setAttribute('aria-activedescendant','');
  }
  searchActiveIndex=-1;
  searchOptions=[];
}

function onSearchInput(){clearTimeout(searchTimer);var q=document.getElementById('searchInput').value.trim();
  var dropdown=document.getElementById('searchDropdown');
  var searchBox=document.getElementById('searchBox');
  var input=document.getElementById('searchInput');
  searchBox.classList.toggle('has-value',!!q);
  if(!q){closeSearch();document.getElementById('searchResults').innerHTML='';return}
  dropdown.classList.add('show');
  input.setAttribute('aria-expanded','true');
  searchTimer=setTimeout(doSearch,500)}

function clearSearch(){
  var input=document.getElementById('searchInput');
  var searchBox=document.getElementById('searchBox');
  input.value='';
  searchBox.classList.remove('has-value');
  closeSearch();
  document.getElementById('searchResults').innerHTML='';
  input.focus();
}

// 键盘导航：方向键移动高亮项，Enter 触发当前项转存，Esc 关闭下拉
function onSearchKeydown(e){
  var dropdown=document.getElementById('searchDropdown');
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    if(!dropdown.classList.contains('show')||!searchOptions.length) return;
    e.preventDefault();
    var next = e.key==='ArrowDown'
      ? Math.min(searchActiveIndex+1, searchOptions.length-1)
      : Math.max(searchActiveIndex-1, 0);
    searchSetActive(next);
  } else if(e.key==='Enter'){
    if(searchActiveIndex>=0 && searchOptions[searchActiveIndex]){
      e.preventDefault();
      var btn=searchOptions[searchActiveIndex].querySelector('.btn');
      if(btn && !btn.disabled){ transferOne(btn); }
      return;
    }
    doSearch();
  } else if(e.key==='Escape'){
    if(dropdown.classList.contains('show')){ e.preventDefault(); closeSearch(); }
  }
}

// 设置第 i 项为高亮（视觉 + ARIA activedescendant）
function searchSetActive(i){
  if(i<0||i>=searchOptions.length) return;
  searchActiveIndex=i;
  searchOptions.forEach(function(o,idx){
    var on = idx===i;
    o.classList.toggle('active', on);
    o.setAttribute('aria-selected', on ? 'true':'false');
    if(on){ o.style.background='rgba(10,132,255,.08)'; o.style.paddingLeft='18px'; }
    else { o.style.background=''; o.style.paddingLeft=''; }
  });
  var opt=searchOptions[i];
  if(!opt.id) opt.id='search-opt-'+i;
  document.getElementById('searchInput').setAttribute('aria-activedescendant', opt.id);
  opt.scrollIntoView({block:'nearest'});
}

async function doSearch(){
  var q=document.getElementById('searchInput').value.trim();
  if(!q)return;
  var dropdown=document.getElementById('searchDropdown');
  var input=document.getElementById('searchInput');
  dropdown.classList.add('show');
  input.setAttribute('aria-expanded','true');
  // 新搜索：重置键盘导航状态
  searchActiveIndex=-1; searchOptions=[]; input.setAttribute('aria-activedescendant','');
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
    var seq = ++searchCheckSeq;
    var checkItems = [];
    d.results.forEach(function(r, i){
      var item = document.createElement('div');
      item.className = 'search-item';
      item.setAttribute('role','option');
      item.setAttribute('id','search-opt-'+i);
      item.setAttribute('aria-selected','false');
      var infoDiv = document.createElement('div');
      infoDiv.style.cssText = 'flex:1;min-width:0';
      var titleDiv = document.createElement('div');
      titleDiv.className = 'search-item-title';
      titleDiv.textContent = r.title;
      var sourceDiv = document.createElement('div');
      sourceDiv.className = 'search-item-source';
      sourceDiv.textContent = r.source||'夸克网盘';
      var badge = document.createElement('div');
      badge.style.cssText = 'margin-top:3px;font-size:12px;color:var(--text3,#999)';
      badge.textContent = '检测中…';
      infoDiv.appendChild(titleDiv);
      infoDiv.appendChild(sourceDiv);
      infoDiv.appendChild(badge);
      var btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-primary';
      btn.textContent = '转存';
      btn.dataset.title = r.title;
      btn.dataset.url = r.url;
      btn.addEventListener('click', function(){ transferOne(this); });
      item.appendChild(infoDiv);
      item.appendChild(btn);
      el.appendChild(item);
      searchOptions.push(item);
      if(r.url){ checkItems.push({url:r.url, badge:badge, btn:btn, seq:seq}); }
    });
    checkSearchLinks(checkItems);}
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
    closeSearch();
  }
});

async function transferOne(btn){
  var title = btn.dataset.title;
  var url = btn.dataset.url;
  // 用剧名作为壳文件夹，复用批量转存“savepath/剧名”的结构，保证转存后目录清晰、能正确显示剧名
  var base = APP_PATHS.search||'/批量转存/手动搜索存';
  var safe = (title||'').replace(/[\/\\:*?"<>|]/g,'').trim().slice(0,80);
  var savepath = base + '/' + (safe || '未命名资源');
  btn.disabled=true; btn.textContent='转存中...';
  try{
    var r = await apiPost('/api/transfer_one',{title:title, shareurl:url, savepath:savepath});
    if(r.success){showToast('已提交转存: '+title,true); btn.textContent='已转存'; btn.className='btn btn-sm btn-outline'}
    else{showToast(r.message||'失败',false); btn.disabled=false; btn.textContent='转存'}
  }catch(e){showToast('请求失败',false); btn.disabled=false; btn.textContent='转存'}
}

// 搜索结果展示后，异步逐条检测链接有效性，用标记提示（先显示后检测，不打断搜索）
async function checkSearchLinks(items){
  if(!items || !items.length) return;
  var CONC = 4;
  for(var i=0;i<items.length;i+=CONC){
    var batch = items.slice(i,i+CONC);
    await Promise.all(batch.map(function(it){ return checkOneLink(it); }));
  }
}
async function checkOneLink(it){
  if(it.seq !== searchCheckSeq) return; // 已有新搜索，丢弃过期回调
  try{
    var d = await apiGet('/api/check_link?url='+encodeURIComponent(it.url), 1);
    if(it.seq !== searchCheckSeq) return;
    if(d.checked === false){
      it.badge.textContent = '检测失败';
      it.badge.style.color = 'var(--text3,#999)';
    } else if(d.valid){
      it.badge.textContent = '✓ 链接正常';
      it.badge.style.color = 'var(--green,#1a9e5f)';
    } else {
      it.badge.textContent = '✗ 链接失效';
      it.badge.style.color = 'var(--red,#e5484d)';
      it.btn.disabled = true;
      it.btn.textContent = '已失效';
      it.btn.className = 'btn btn-sm btn-outline';
    }
  }catch(e){
    if(it.seq !== searchCheckSeq) return;
    it.badge.textContent = '检测失败';
    it.badge.style.color = 'var(--text3,#999)';
  }
}
