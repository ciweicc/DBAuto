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
// 用户诉求：「搜出来点进去不能看集数全不全」→ 二次澄清：「不需要显示集数，
// 显示集数会做得非常复杂，而且故障率很高。只需要显示这条链接里面的具体文件
// 内容层级就可以了 —— 点「文件」按钮能看到里面有什么文件，我自己判断多少集」。
//
// 所以这里的**唯一职责**是把分享里的文件清单原样列出来（目录层级 + 文件名 +
// 体积）。不做集号解析、不下「全不全」的结论：文件名命名千奇百怪，自动判定
// 必然误报，而误报比不报更贵（用户会去排查一个不存在的缺集）。
var searchDetailSeq = 0;
// 已取回的分享清单（url -> 归一化响应）。
// 同一次搜索里再次展开同一条走这里，零请求。
var searchDetailCache = {};

// 面板顶部的一句尺寸摘要：只报可数的东西（条目数 / 目录数 / 视频数），
// 不做集数推断。这是用户要的「我看得出有多少集」的原料，不是结论。
function detailCountText(res){
  if(!res || !res.ok) return '';
  var total = res.total || 0;
  if(!total) return '';
  var parts = ['共 ' + total + ' 个条目'];
  if(res.dir_count) parts.push(res.dir_count + ' 个目录');
  if(res.video_count) parts.push(res.video_count + ' 个视频');
  return parts.join(' · ');
}

// 「文件」按钮就地转圈（请求中）。
// 清单接口打的是 QAS，每次都要拿 Cookie 向夸克**实查**，慢且严格限速，
// 所以点开一条不能锁死整行：只把这一颗按钮变成转圈，其它结果照常可点。
function setDetailBtnBusy(btn, busy){
  if(!btn) return;
  if(busy){
    btn.disabled = true;
    btn.dataset.label = btn.textContent;
    btn.textContent = '';
    btn.classList.add('is-busy');
    var sp = document.createElement('span');
    sp.className = 'btn-spinner';
    sp.setAttribute('aria-hidden','true');
    btn.appendChild(sp);
  } else {
    btn.disabled = false;
    btn.classList.remove('is-busy');
    btn.textContent = btn.dataset.label || '文件';
    delete btn.dataset.label;
  }
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
  sum.textContent = detailCountText(res) || ('共 ' + files.length + ' 个条目');
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

// 展开/收起一条搜索结果的文件清单。
//
// 取数时机：**点开才取**（issue #88 第五轮）。
//   - 结果直出只带标题 / 来源 / 链接徽标，不等清单接口；
//   - 只有用户点了「文件」的那一条才去取清单，按钮就地转圈；
//   - 已取过的复用 searchDetailCache，二次展开零请求。
// 过去是「结果一出就把 20 条分享全拉一遍」，QAS 逐条实查 + 限速，
// 队列长达几十秒，而用户往往只点开其中一两条 —— 那些请求纯属浪费。
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
  var cached = searchDetailCache[url];
  if(cached){
    // 预热已经拿到过 —— 直接渲染，用户点开就是即时反馈
    panel.dataset.loaded = '1';
    renderShareDetail(panel, cached);
    return;
  }
  var seq = ++searchDetailSeq;
  panel.textContent = '';
  var loading = document.createElement('div');
  loading.className = 'search-detail-msg search-detail-loading';
  loading.textContent = '正在读取文件清单…';
  panel.appendChild(loading);
  setDetailBtnBusy(btn, true);
  try{
    var d = await apiGet('/api/share_detail?url=' + encodeURIComponent(url), 1);
    if(seq !== searchDetailSeq) return;   // 已有新展开请求，丢弃过期回调
    panel.dataset.loaded = '1';
    searchDetailCache[url] = d;   // 让后续复用同一份数据
    renderShareDetail(panel, d);
  }catch(e){
    if(seq !== searchDetailSeq) return;
    // 失败**不写缓存**：缓存下来重试也只会拿到同一个失败
    renderShareDetail(panel, {ok:false, message:'读取分享文件失败：' + (e.message||'网络错误')});
  }finally{
    if(seq === searchDetailSeq) setDetailBtnBusy(btn, false);
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
  searchDetailCache = {};   // 新搜索：上一轮取回的清单作废
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
  // 阶段提示：只有**搜索**这一段需要（用户看不到任何其它反馈）。
  // 链接检测有逐条徽标、清单有按钮转圈，各自的进度长在自己身上更准。
  var stage = document.createElement('div');
  stage.style.cssText = 'width:100%;text-align:center;font-size:12px;color:var(--text3);padding:2px 0 8px';
  stage.textContent = '正在搜索…';
  el.appendChild(stage);
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
      // 「文件」按钮：展开该分享的文件清单。
      // 只列文件（目录 / 文件名 / 体积），不解析集数 —— 用户明确表示
      // 「不需要显示集数……只显示文件内容层级就可以了」（issue #88 第三轮）。
      // 无链接的结果不给这个入口（没东西可预览）。
      if(r.url){
        var fileBtn = document.createElement('button');
        fileBtn.className = 'btn btn-sm btn-outline search-detail-toggle';
        fileBtn.type = 'button';
        fileBtn.textContent = '文件';
        fileBtn.dataset.url = r.url;
        fileBtn.setAttribute('aria-expanded','false');
        fileBtn.title = '展开这个分享里的文件清单';
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
      if(r.url){
        checkItems.push({url:r.url, badge:badge, btn:btn, seq:seq});
      }
    });
    // 只发一次批量链接检测（一次请求拿全部结果）。清单一律等用户点开再取
    // —— 一次搜索的上游请求从「1 + 1 + N」降到「1 + 1」。预热等待的
    // 「正在检测链接…」阶段提示也随之删掉：链接徽标自己会从「检测中…」
    // 变成结论，再挂一句全局提示只会让人以为整块还没加载完。
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

// 搜索结果展示后检测链接有效性，用标记提示（先显示后检测，不打断搜索）。
//
// 这里从「逐条请求 + 轮询」改成了**一次批量请求**（issue #88 第三轮：
// 「有办法提高它的搜索速度吗？当前的搜索速度实在是有点慢」）：
// 过去 N 条结果 = N 个 /api/check_link + N 轮 status 轮询（每条最多 20 次）
// = 上千次 HTTP 往返；现在一次 /api/check_links 拿全部结果。
// 后端 PanSou check/links 本来就支持一次传多条，之前只是没有用上。
//
// 批量失败时**不标红**：整批拿到不结果就显示「检测失败」，
// 让用户自己判断，而不是把一批有效链接全标成失效。
async function checkSearchLinks(items){
  if(!items || !items.length) return;
  var seq = items[0].seq;
  var byUrl = {};
  items.forEach(function(it){ byUrl[it.url] = it; });
  var urls = items.map(function(it){ return it.url; });
  var results = null;
  try{
    var d = await apiGet('/api/check_links?urls=' + encodeURIComponent(urls.join(',')), 1);
    if(seq !== searchCheckSeq) return;
    if(d && d.success && d.results) results = d.results;
  }catch(e){
    if(seq !== searchCheckSeq) return;
  }
  if(!results){
    items.forEach(function(it){
      if(it.badge){ it.badge.textContent = '检测失败'; it.badge.style.color = 'var(--text3,#999)'; }
    });
    return;
  }
  urls.forEach(function(u){
    var it = byUrl[u];
    if(!it || !it.badge) return;
    var r = results[u];
    if(!r || r.checked === false || r.valid === null || r.valid === undefined){
      it.badge.textContent = '检测失败';
      it.badge.style.color = 'var(--text3,#999)';
      return;
    }
    if(r.valid){
      it.badge.textContent = '✓ 链接正常';
      it.badge.style.color = 'var(--green)';
    } else {
      it.badge.textContent = '✗ 链接失效';
      it.badge.style.color = 'var(--red)';
      it.btn.disabled = true;
      it.btn.textContent = '已失效';
      it.btn.className = 'btn btn-sm btn-outline';
    }
  });
}
