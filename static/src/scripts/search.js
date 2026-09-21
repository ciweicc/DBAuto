// ============ Search ============
var searchTimer=null;var searchAbort=null;var searchCheckSeq=0;
// 键盘导航状态：当前高亮的结果项索引 + 结果项 DOM 引用
var searchActiveIndex=-1;
var searchOptions=[];

// 平台相关的修饰键显示：macOS 用 ⌘，其余用 Ctrl。
// 之前的提示一律写 ⌘K，Windows 用户（见 issue #78 截图）看到的是一颗
// 自己键盘上没有的键 —— 「无效」有一半是这么来的。
function searchModKeyLabel(){
  try{
    var p=(navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    if(/mac|iphone|ipad|ipod/i.test(p)) return '\u2318';
  }catch(e){}
  return 'Ctrl';
}
// 把带平台修饰键的 kbd 提示刷成当前平台的样子（macOS ⌘ / 其它 Ctrl）。
// 搜索框的徽标、命令面板 footer 的「快捷键」提示共用这一套，避免
// footer 里死写 ⌘ 而 Windows 用户按不出来（issue #78 的截图就是 Windows）。
function syncModKeyLabels(){
  var mod=searchModKeyLabel();
  document.querySelectorAll('.cmd-modkey').forEach(function(el){
    el.textContent=mod+'/';
  });
}
// 把搜索框上的快捷键徽标刷成当前平台的样子（桌面 ⌘K / 其它 Ctrl+K）
function syncSearchKbd(){
  var kbd=document.querySelector('.search-kbd');
  if(!kbd) return;
  var mod=searchModKeyLabel();
  kbd.textContent=(mod==='\u2318') ? '\u2318K' : mod+'+K';
  var tip='搜索资源（'+mod+'+K 聚焦本框）';
  var input=document.getElementById('searchInput');
  if(input) input.title=tip;
  kbd.setAttribute('title',tip);
  kbd.setAttribute('aria-hidden','true');
  syncModKeyLabels();
}
// 聚焦搜索框（⌘K / Ctrl+K 的目标动作，issue #78）。
// 窄屏时搜索框默认隐藏，需要先把移动态搜索层打开，否则「聚焦」聚焦到
// 一个 display:none 的输入上 = 按了没反应。
function focusSearchBox(){
  var w=document.querySelector('.search-wrapper');
  var input=document.getElementById('searchInput');
  if(!w || !input) return false;
  if(getComputedStyle(w).display==='none') w.classList.add('mobile-show');
  try{ input.focus(); input.select(); }catch(e){ return false; }
  return true;
}

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
    var main = o.querySelector('.search-item-main') || o;
    if(on){ main.style.background='rgba(10,132,255,.08)'; main.style.paddingLeft='18px'; }
    else { main.style.background=''; main.style.paddingLeft=''; }
  });
  var opt=searchOptions[i];
  if(!opt.id) opt.id='search-opt-'+i;
  document.getElementById('searchInput').setAttribute('aria-activedescendant', opt.id);
  opt.scrollIntoView({block:'nearest'});
}

// ---------- 分享文件清单预览（issue #88）----------
// 用户诉求：「搜索资源的功能搜出来点进去不能看集数全不全」。
// 过去搜索结果只有一行标题 + 链接是否有效，想确认「是不是全集」只能先转存
// 再去媒体库翻 —— 试错成本高。QAS 的 get_share_detail 本来就把分享根目录的
// 文件清单拉回来了，只是被丢掉；这里把它展开给用户看。
var searchDetailSeq = 0;

// 集号摘要的后端文案：把「认出的集号 / 缺口」翻译成一句人话。
// 刻意不下「是否全集」的结论（后端只回可观测量），措辞必须保持中性。
function epSummaryText(res){
  if(!res || !res.ok) return '';
  if(!res.video_count) return '';
  var ep = res.episodes || {};
  var nums = ep.numbers || [];
  if(nums.length < 2) return '共 ' + res.video_count + ' 个视频文件';
  var txt = '共 ' + res.video_count + ' 个视频文件 · 检出集号 ' + nums[0] + '-' + nums[nums.length-1];
  if(ep.continuous) txt += ' · 未见断档';
  else txt += ' · 缺 ' + (ep.missing||[]).join('、');
  return txt;
}

// 渲染文件清单面板内容（成功 / 空 / 失败三态）。
function renderShareDetail(panel, res){
  panel.textContent = '';
  if(!res || !res.ok){
    var err = document.createElement('div');
    err.className = 'search-detail-msg';
    err.textContent = (res && res.message) || '读取分享文件失败';
    panel.appendChild(err);
    return;
  }
  var files = res.files || [];
  if(!files.length){
    var empty = document.createElement('div');
    empty.className = 'search-detail-msg';
    empty.textContent = '这个分享里没有文件（可能是空的目录）';
    panel.appendChild(empty);
    return;
  }
  var sum = document.createElement('div');
  sum.className = 'search-detail-sum';
  sum.textContent = epSummaryText(res) || ('共 ' + res.total + ' 个条目');
  panel.appendChild(sum);

  var list = document.createElement('div');
  list.className = 'search-detail-list';
  files.forEach(function(f){
    var row = document.createElement('div');
    row.className = 'search-detail-row' + (f.dir ? ' is-dir' : '');
    var name = document.createElement('span');
    name.className = 'search-detail-name';
    name.textContent = f.name;
    name.title = f.name;
    row.appendChild(name);
    var meta = document.createElement('span');
    meta.className = 'search-detail-size';
    meta.textContent = f.dir ? '目录' : fmtSize(f.size);
    row.appendChild(meta);
    list.appendChild(row);
  });
  panel.appendChild(list);
  if(res.truncated){
    var more = document.createElement('div');
    more.className = 'search-detail-msg';
    more.textContent = '仅显示前 ' + files.length + ' 个，共 ' + res.total + ' 个';
    panel.appendChild(more);
  }
}

// 展开/收起一条搜索结果的文件清单。首次展开才请求（懒加载），二次展开走缓存。
async function toggleShareDetail(btn){
  var item = btn.closest('.search-item');
  if(!item) return;
  var panel = item.querySelector('.search-detail');
  if(!panel) return;
  if(panel.classList.contains('show')){
    panel.classList.remove('show');
    btn.textContent = '文件';
    btn.setAttribute('aria-expanded','false');
    return;
  }
  panel.classList.add('show');
  btn.textContent = '收起';
  btn.setAttribute('aria-expanded','true');
  if(panel.dataset.loaded === '1') return;
  var url = btn.dataset.url;
  if(!url) return;
  var seq = ++searchDetailSeq;
  panel.textContent = '';
  var loading = document.createElement('div');
  loading.className = 'search-detail-msg';
  loading.textContent = '正在读取文件清单…';
  panel.appendChild(loading);
  try{
    var d = await apiGet('/api/share_detail?url=' + encodeURIComponent(url), 1);
    if(seq !== searchDetailSeq) return;   // 已有新展开请求，丢弃过期回调
    panel.dataset.loaded = '1';
    renderShareDetail(panel, d);
  }catch(e){
    if(seq !== searchDetailSeq) return;
    renderShareDetail(panel, {ok:false, message:'读取分享文件失败：' + (e.message||'网络错误')});
  }
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
      var emptyWrap = renderEmptyState({icon:'icon-search', title:'没有找到相关资源', desc:'试试换个关键词，或检查拼写<br>也可以去「手动转存」从榜单中选择'});
      emptyWrap.style.padding = '28px 20px';
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
      // 主行：标题 / 来源 / 链接检测徽标 + 操作按钮。
      // 高亮位移（searchSetActive 里的 paddingLeft）作用在这一层，
      // 不能作用在 .search-item 上 —— 那样展开的文件清单会跟着一起缩进。
      var main = document.createElement('div');
      main.className = 'search-item-main';
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
      var actions = document.createElement('div');
      actions.className = 'search-item-actions';
      // 「文件」按钮：展开该分享的文件清单，用来判断集数全不全（issue #88）。
      // 无链接的结果不给这个入口（没东西可预览）。
      if(r.url){
        var fileBtn = document.createElement('button');
        fileBtn.className = 'btn btn-sm btn-outline search-detail-toggle';
        fileBtn.type = 'button';
        fileBtn.textContent = '文件';
        fileBtn.dataset.url = r.url;
        fileBtn.setAttribute('aria-expanded','false');
        fileBtn.title = '查看分享里的文件清单（判断集数全不全）';
        fileBtn.addEventListener('click', function(e){ e.stopPropagation(); toggleShareDetail(this); });
        actions.appendChild(fileBtn);
      }
      var btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-primary';
      btn.textContent = '转存';
      btn.dataset.title = r.title;
      btn.dataset.url = r.url;
      btn.addEventListener('click', function(){ transferOne(this); });
      actions.appendChild(btn);
      main.appendChild(infoDiv);
      main.appendChild(actions);
      item.appendChild(main);
      var panel = document.createElement('div');
      panel.className = 'search-detail';
      item.appendChild(panel);
      el.appendChild(item);
      searchOptions.push(item);
      if(r.url){ checkItems.push({url:r.url, badge:badge, btn:btn, seq:seq}); }
    });
    checkSearchLinks(checkItems);}
  catch(e){
    if(e.name==='AbortError')return;
    el.textContent='';
    var failWrap = renderErrorState({title:'搜索失败', desc: e.message||'网络错误'});
    failWrap.style.padding = '24px';
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

  // 轮询辅助：检测任务入队后反复查询 /api/check_link/status 直至终态。
  // 采用 async/await + 循环（非 setInterval 回调风格），每次轮询前校验 seq 防呆。
  function delay(ms){ return new Promise(function(res){ setTimeout(res, ms); }); }
  async function pollStatus(taskId){
    var MAX_RETRY = 20;
    var INTERVAL = 1500;
    var last = null;
    for(var i = 0; i < MAX_RETRY; i++){
      if(it.seq !== searchCheckSeq) return null; // 已有新搜索，丢弃过期轮询
      last = await apiGet('/api/check_link/status?task_id=' + encodeURIComponent(taskId), 1);
      if(it.seq !== searchCheckSeq) return null;
      // 终态：done / error / unknown / busy 不再轮询
      if(last.state === 'done' || last.state === 'error' || last.state === 'unknown' || last.state === 'busy'){
        return last;
      }
      // pending / running：间隔后继续轮询
      if(i < MAX_RETRY - 1){
        if(it.seq !== searchCheckSeq) return null;
        await delay(INTERVAL);
      }
    }
    // 超过最大重试次数仍未完成 → 中性「检测超时」，不标红失效
    if(last) last.state = 'timeout';
    return last;
  }

  try{
    // 搜索结果均来自盘搜，链接检测必须走 PanSou 校验（source=pansou），
    // 否则会被错误地用 QAS 验证、把有效盘搜链接误报为「✗ 链接失效」。
    var d = await apiGet('/api/check_link?url=' + encodeURIComponent(it.url) + '&source=pansou', 1);
    if(it.seq !== searchCheckSeq) return;

    var state = d.state;
    if(state === 'pending' || state === 'running'){
      // 任务入队，需轮询 status 接口直到终态
      if(!d.task_id){
        it.badge.textContent = '检测失败';
        it.badge.style.color = 'var(--text3,#999)';
        return;
      }
      var final = await pollStatus(d.task_id);
      if(it.seq !== searchCheckSeq) return;
      if(final === null) return; // 已过期，静默丢弃
      if(final.state === 'timeout'){
        it.badge.textContent = '检测超时';
        it.badge.style.color = 'var(--text3,#999)';
        return;
      }
      // 终态结果覆盖，继续走下方渲染逻辑
      d = final;
      state = final.state;
    }

    if(state === 'busy'){
      it.badge.textContent = '检测繁忙';
      it.badge.style.color = 'var(--text3,#999)';
      return;
    }
    if(state === 'error' || state === 'unknown'){
      it.badge.textContent = '检测失败';
      it.badge.style.color = 'var(--text3,#999)';
      return;
    }

    // state === 'done'（或旧后端无 state 字段）：按现有逻辑渲染 ✓/✗/检测失败
    if(d.checked === false){
      it.badge.textContent = '检测失败';
      it.badge.style.color = 'var(--text3,#999)';
    } else if(d.valid){
      it.badge.textContent = '✓ 链接正常';
      it.badge.style.color = 'var(--green)';
    } else {
      it.badge.textContent = '✗ 链接失效';
      it.badge.style.color = 'var(--red)';
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
