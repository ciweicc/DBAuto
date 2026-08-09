// ============ TMDB Page ============
var tmdbState = {
  page: 1,
  total_pages: 1,
  media_type: 'movie',
  list_type: 'trending',
  genre_id: 0,
  items: [],
  selected: {},
  providers: [],        // 已选流媒体平台 id 列表
  providerAnchor: -1,   // Shift 范围选择的锚点索引
  providersList: [],    // 当前地区可用的平台完整列表
  options: null,
  genres: [],
  initialized: false,
  loadingMore: false,   // 是否正在加载下一页（防止重复触发）
  allLoaded: false,     // 是否已加载完所有页
  _scrollBound: false,  // 滚动监听是否已绑定（防止重复绑定）
  _renderedCount: 0     // 已渲染到 grid 的卡片数量（用于增量追加）
};

function tmdbPosterFallback(img){
  img.outerHTML = '<div class="tmdb-poster" style="display:flex;align-items:center;justify-content:center;color:var(--text3)"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#icon-movie"/></svg></div>';
}

async function initTmdbPage(){
  // 点击页面其它区域或按 Esc 关闭平台下拉（仅绑定一次）
  if(!tmdbState._outsideBound){
    document.addEventListener('click', function(e){
      if(e.key === 'Escape'){ closeProviderDropdown(); return; }
      var wrap = document.getElementById('tmdbProviderWrap');
      if(wrap && !wrap.contains(e.target)) closeProviderDropdown();
    });
    tmdbState._outsideBound = true;
  }
  if(!tmdbState.initialized){
    // 加载选项
    try{
      var opts = await apiGet('/api/tmdb/options');
      tmdbState.options = opts;
      // 填充地区（国家）与语言
      var regionSel = document.getElementById('tmdbRegion');
      regionSel.innerHTML = opts.countries.map(function(r){return '<option value="'+r.code+'">'+r.name+'</option>'}).join('');
      var langSel = document.getElementById('tmdbLanguage');
      if(langSel) langSel.innerHTML = opts.languages.map(function(l){return '<option value="'+l.code+'">'+l.name+'</option>'}).join('');
      // 填充列表类型
      updateTmdbListTypeOptions(opts.movie_list_types);
      tmdbState.initialized = true;
    }catch(e){
      console.error('TMDB init error', e);
    }
  }
  // 检查 API Key
  try{
    var cfg = await apiGet('/api/config');
    if(cfg.tmdb_api_key && cfg.tmdb_api_key !== '***'){
      document.getElementById('tmdbKeyWarning').style.display = 'none';
    }else if(cfg.tmdb_api_key === '***'){
      document.getElementById('tmdbKeyWarning').style.display = 'none';
    }else{
      document.getElementById('tmdbKeyWarning').style.display = 'flex';
      document.getElementById('tmdbGrid').innerHTML = '<div class="tmdb-empty">请先在设置中配置 TMDB API Key</div>';
      return;
    }
  }catch(e){}
  // 加载类型和列表
  await loadTmdbGenres();
  await loadTmdbList();
  // 绑定无限滚动监听（只绑定一次）
  bindTmdbScroll();
}

function updateTmdbListTypeOptions(listTypes){
  var sel = document.getElementById('tmdbListType');
  var current = sel.value || 'trending';
  sel.innerHTML = listTypes.map(function(l){return '<option value="'+l.code+'">'+l.name+'</option>'}).join('');
  // 尝试保持当前选择
  if([].some.call(sel.options, function(o){return o.value === current})){
    sel.value = current;
  }
}

async function loadTmdbGenres(){
  var mt = document.getElementById('tmdbMediaType').value;
  if(mt === tmdbState.media_type && tmdbState.genres.length) return;
  tmdbState.media_type = mt;
  try{
    var d = await apiGet('/api/tmdb/genres?media_type='+mt);
    tmdbState.genres = d.genres || [];
    var chips = document.getElementById('tmdbGenreChips');
    var html = '<button type="button" class="chip on" data-gid="0" onclick="toggleTmdbGenre(this)">全部</button>';
    html += tmdbState.genres.map(function(g){return '<button type="button" class="chip" data-gid="'+g.id+'" onclick="toggleTmdbGenre(this)">'+g.name+'</button>'}).join('');
    chips.innerHTML = html;
    tmdbState.genre_id = 0;
  }catch(e){
    console.error('TMDB genres error', e);
  }
}

function toggleTmdbGenre(el){
  // 如果点击的条目已选中，无需重新加载
  if(el.classList.contains('on')) return;
  var chips = document.querySelectorAll('#tmdbGenreChips .chip');
  chips.forEach(function(c){c.classList.remove('on')});
  el.classList.add('on');
  tmdbState.genre_id = parseInt(el.dataset.gid) || 0;
  tmdbState.page = 1;
  loadTmdbList();
}

async function loadTmdbProviders(){
  var mt = document.getElementById('tmdbMediaType').value;
  var country = document.getElementById('tmdbRegion').value || 'US';
  var panel = document.getElementById('tmdbProviderPanel');
  try{
    var d = await apiGet('/api/tmdb/providers?media_type='+mt+'&region='+country);
    var list = d.providers || [];
    tmdbState.providersList = list;
    // 地区/分类切换后平台集合变化，清空已选并重置锚点
    tmdbState.providers = [];
    tmdbState.providerAnchor = -1;
    if(!list.length){
      panel.innerHTML = '<div class="tmdb-ms-hint">该地区暂无可用平台</div>';
      updateProviderLabel();
      return;
    }
    var html = list.map(function(p, i){
      return '<div class="tmdb-ms-option" role="option" aria-selected="false" data-pid="'+p.id+'" data-idx="'+i+'" onclick="toggleProviderOption(this, event)">'
        + '<span class="ms-check"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg></span>'
        + '<span class="ms-name">'+esc(p.name)+'</span></div>';
    }).join('');
    html += '<div class="tmdb-ms-clear" onclick="clearProviderSelection(event)">清除全部选择</div>';
    html += '<div class="tmdb-ms-hint">点击多选；按住 Shift 点选可批量选中一段</div>';
    panel.innerHTML = html;
    updateProviderLabel();
  }catch(e){
    console.error('TMDB providers error', e);
    panel.innerHTML = '<div class="tmdb-ms-hint">平台列表加载失败</div>';
  }
}

/* ---- 流媒体平台多选 + Shift 范围选择 ---- */
function toggleProviderDropdown(ev){
  ev.stopPropagation();
  var btn = document.getElementById('tmdbProviderBtn');
  if(btn.disabled) return;
  var panel = document.getElementById('tmdbProviderPanel');
  var open = panel.style.display === 'block';
  panel.style.display = open ? 'none' : 'block';
  btn.setAttribute('aria-expanded', open ? 'false' : 'true');
}

function closeProviderDropdown(){
  var panel = document.getElementById('tmdbProviderPanel');
  if(panel) panel.style.display = 'none';
  var btn = document.getElementById('tmdbProviderBtn');
  if(btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleProviderOption(el, ev){
  ev.stopPropagation();
  var idx = parseInt(el.dataset.idx, 10);
  var pid = parseInt(el.dataset.pid, 10);
  var selIdx = tmdbState.providers.indexOf(pid);
  if(ev.shiftKey && tmdbState.providerAnchor >= 0 && tmdbState.providerAnchor !== idx){
    // 以锚点为准，把锚点到当前项这一段统一设为“目标状态”
    var a = Math.min(tmdbState.providerAnchor, idx);
    var b = Math.max(tmdbState.providerAnchor, idx);
    var targetOn = selIdx < 0; // 点击项当前未选 -> 整段选中；已选 -> 整段取消
    for(var i=a;i<=b;i++){
      var op = tmdbState.providersList[i];
      if(!op) continue;
      var oi = tmdbState.providers.indexOf(op.id);
      if(targetOn && oi < 0) tmdbState.providers.push(op.id);
      else if(!targetOn && oi >= 0) tmdbState.providers.splice(oi, 1);
    }
  }else{
    // 普通 / Ctrl / Cmd 点击：切换单项并设为新锚点
    if(selIdx < 0) tmdbState.providers.push(pid);
    else tmdbState.providers.splice(selIdx, 1);
    tmdbState.providerAnchor = idx;
  }
  renderProviderSelection();
}

function clearProviderSelection(ev){
  if(ev) ev.stopPropagation();
  tmdbState.providers = [];
  tmdbState.providerAnchor = -1;
  renderProviderSelection();
}

function renderProviderSelection(){
  var options = document.querySelectorAll('#tmdbProviderPanel .tmdb-ms-option');
  var selSet = {};
  tmdbState.providers.forEach(function(id){ selSet[id] = true; });
  options.forEach(function(o){
    var pid = parseInt(o.dataset.pid, 10);
    var on = !!selSet[pid];
    o.classList.toggle('on', on);
    o.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  updateProviderLabel();
  if(tmdbState.list_type === 'discover'){
    tmdbState.page = 1;
    loadTmdbList();
  }
}

function updateProviderLabel(){
  var btn = document.getElementById('tmdbProviderBtn');
  var label = document.getElementById('tmdbProviderLabel');
  if(!btn || !label) return;
  var n = tmdbState.providers.length;
  if(!n){
    label.textContent = '全部平台';
    btn.classList.remove('has-sel');
  }else{
    label.textContent = '已选 ' + n + ' 个平台';
    btn.classList.add('has-sel');
  }
}

function onTmdbFilterChange(){
  var mt = document.getElementById('tmdbMediaType').value;
  // 如果切换了 media_type，更新列表类型选项
  if(mt !== tmdbState.media_type){
    var listTypes = mt === 'movie' ? tmdbState.options.movie_list_types : tmdbState.options.tv_list_types;
    updateTmdbListTypeOptions(listTypes);
  }
  // 更新列表选项后重新获取 lt，确保使用最新的下拉框值
  var lt = document.getElementById('tmdbListType').value;
  // 如果用户手动从 discover 切换到其他榜单类型，清除 discover 专属筛选条件
  if(lt !== 'discover' && tmdbState.list_type === 'discover'){
    tmdbState.genre_id = 0;
    var chips = document.querySelectorAll('#tmdbGenreChips .chip');
    chips.forEach(function(c){c.classList.remove('on')});
    var allChip = document.querySelector('#tmdbGenreChips .chip[data-gid="0"]');
    if(allChip) allChip.classList.add('on');
    document.getElementById('tmdbMinRating').value = 0;
    document.getElementById('tmdbYear').value = '';
  }
  tmdbState.list_type = lt;
  // 如果不是 discover，禁用排序/评分/年份/类型筛选（仅 discover 有效）
  var isDiscover = lt === 'discover';
  document.getElementById('tmdbSort').disabled = !isDiscover;
  document.getElementById('tmdbMinRating').disabled = !isDiscover;
  document.getElementById('tmdbYear').disabled = !isDiscover;
  document.getElementById('tmdbProviderBtn').disabled = !isDiscover;
  if(isDiscover){
    // 进入 discover 或地区/分类切换时，刷新当前地区可用的流媒体平台列表
    loadTmdbProviders();
  }else{
    clearProviderSelection();
    closeProviderDropdown();
  }
  // 切换 media_type 时重新加载类型
  if(mt !== tmdbState.media_type){
    tmdbState.media_type = mt;
    tmdbState.genres = [];
    loadTmdbGenres().then(function(){tmdbState.page = 1; loadTmdbList()});
  }else{
    tmdbState.page = 1;
    loadTmdbList();
  }
}

// 读取当前筛选条件并拼装 /api/tmdb/list 请求参数
function buildTmdbListParams(){
  var mt = document.getElementById('tmdbMediaType').value;
  var lt = document.getElementById('tmdbListType').value;
  var region = document.getElementById('tmdbRegion').value;
  var language = document.getElementById('tmdbLanguage') ? document.getElementById('tmdbLanguage').value : '';
  var sort = document.getElementById('tmdbSort').value;
  var minRating = parseFloat(document.getElementById('tmdbMinRating').value) || 0;
  var year = parseInt(document.getElementById('tmdbYear').value) || 0;
  var genreId = tmdbState.genre_id || 0;
  var provider = tmdbState.providers.join(',');

  var params = 'media_type='+mt+'&list_type='+lt+'&page='+tmdbState.page;
  if(lt === 'discover'){
    if(genreId) params += '&genre_id='+genreId;
    if(year) params += '&year='+year;
    if(minRating) params += '&min_rating='+minRating;
    if(provider) params += '&watch_providers='+provider;
    params += '&sort_by='+sort;
  }
  if(region) params += '&country='+region;
  if(language) params += '&language='+language;
  return params;
}

async function loadTmdbList(append){
  append = append || false;
  var grid = document.getElementById('tmdbGrid');

  // ---- 增量加载（无限滚动）----
  if(append){
    // 防护：已在加载 / 已全部加载完 / 没有下一页 时直接返回
    if(tmdbState.loadingMore || tmdbState.allLoaded || tmdbState.page >= tmdbState.total_pages) return;
    tmdbState.loadingMore = true;
    // 底部“加载更多”指示器（不清除现有 grid）
    grid.insertAdjacentHTML('beforeend',
      '<div id="tmdbLoadMore" style="grid-column:1/-1;display:flex;align-items:center;justify-content:center;gap:10px;padding:18px;color:var(--text3);font-size:13px">加载更多…</div>');
    tmdbState.page++;
    var aparams = buildTmdbListParams();
    try{
      var ad = await apiGet('/api/tmdb/list?'+aparams);
      var newItems = ad.items || [];
      // 按 id 去重（切换筛选条件时不同页可能重叠）
      var seen = {};
      tmdbState.items.forEach(function(it){ seen[it.id] = true; });
      var deduped = newItems.filter(function(it){
        if(seen[it.id]) return false;
        seen[it.id] = true;
        return true;
      });
      tmdbState.items = tmdbState.items.concat(deduped);
      if(ad.total_pages) tmdbState.total_pages = ad.total_pages;
      var alm = document.getElementById('tmdbLoadMore');
      if(alm) alm.remove();
      renderTmdbGrid(true);
      renderTmdbPagination();
      tmdbState.loadingMore = false;
      if(tmdbState.page >= tmdbState.total_pages) tmdbState.allLoaded = true;
    }catch(e){
      // 追加失败时仅停止并隐藏指示器，不清除已渲染内容
      var elm = document.getElementById('tmdbLoadMore');
      if(elm) elm.remove();
      tmdbState.loadingMore = false;
      console.error('TMDB append load error', e);
    }
    return;
  }

  // ---- 全新加载（替换）----
  tmdbState.items = [];
  tmdbState.allLoaded = false;
  tmdbState.loadingMore = false;

  var mt = document.getElementById('tmdbMediaType').value;
  var lt = document.getElementById('tmdbListType').value;
  var minRating = parseFloat(document.getElementById('tmdbMinRating').value) || 0;
  var year = parseInt(document.getElementById('tmdbYear').value) || 0;
  var genreId = tmdbState.genre_id || 0;

  // 如果不是 discover，但用户选了类型/评分/年份，自动切换到 discover
  if(lt !== 'discover' && (genreId > 0 || minRating > 0 || year > 0)){
    document.getElementById('tmdbListType').value = 'discover';
    lt = 'discover';
    tmdbState.list_type = 'discover';
    onTmdbFilterChange();
    return;
  }

  var sk=''; for(var si=0;si<12;si++){ sk += '<div class="tmdb-skeleton"></div>'; }
  grid.innerHTML = sk;

  var params = buildTmdbListParams();

  try{
    var d = await apiGet('/api/tmdb/list?'+params);
    tmdbState.items = d.items || [];
    tmdbState.total_pages = d.total_pages || 1;
    renderTmdbGrid(false);
    renderTmdbPagination();
    if(d.error){
      var msg = String(d.error);
      var isNet = /SSL|EOF|Max retries|Connection|timed out|Timeout|ENOTFOUND|getaddrinfo/i.test(msg);
      var hint = isNet
        ? '<br><span style="font-size:12px;opacity:.75;line-height:1.6">无法连接 TMDB，多为网络/代理问题。请到「设置 → TMDB 数据源」填写代理地址（如 http://127.0.0.1:7890），或改用可访问的 API 地址。</span>'
        : '';
      grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">⚠ '+esc(msg)+hint+'</div>';
    }
  }catch(e){
    grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">加载失败: '+esc(e.message||'')+'<br><span style="font-size:12px;opacity:.7">请检查 TMDB API Key 是否正确，以及网络是否能访问 themoviedb.org；若被网络限制，可在设置中配置代理。</span></div>';
  }
}

// 单张卡片 HTML（供全新渲染与增量追加复用）
function tmdbCardHtml(item){
  var sel = tmdbState.selected[item.id] ? ' selected' : '';
  var poster = item.poster || '';
  var posterHtml = poster
    ? '<img class="tmdb-poster" src="'+esc(poster)+'" loading="lazy" decoding="async" alt="'+esc(item.title)+'" draggable="false" ondragstart="return false" onerror="tmdbPosterFallback(this)">'
    : '<div class="tmdb-poster" style="display:flex;align-items:center;justify-content:center;color:var(--text3)"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#icon-movie"/></svg></div>';
  var ratingHtml = item.rating > 0
    ? '<div class="tmdb-rating-badge">★ '+item.rating.toFixed(1)+'</div>'
    : '';
  var yearStr = item.year ? item.year : '';
  var votesStr = item.votes > 0 ? (item.votes >= 1000 ? (item.votes/1000).toFixed(1)+'k' : item.votes) + '票' : '';
  var metaParts = [];
  if(yearStr) metaParts.push(yearStr);
  if(votesStr) metaParts.push(votesStr);
  return '<div class="tmdb-card'+sel+'" role="button" tabindex="0" aria-pressed="'+(sel?'true':'false')+'" data-id="'+item.id+'" onclick="toggleTmdbCard(this,'+item.id+')">'+
    '<div class="tmdb-poster-wrap">'+posterHtml+ratingHtml+'</div>'+
    '<div class="tmdb-info">'+
      '<div class="tmdb-title">'+esc(item.title)+'</div>'+
      '<div class="tmdb-meta">'+metaParts.join(' · ')+'</div>'+
      (item.overview ? '<div class="tmdb-overview">'+esc(item.overview)+'</div>' : '')+
    '</div>'+
  '</div>';
}

function renderTmdbGrid(append){
  append = append || false;
  var grid = document.getElementById('tmdbGrid');

  // 全新渲染
  if(!append){
    if(!tmdbState.items.length){
      grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">暂无数据</div>';
      tmdbState._renderedCount = 0;
      return;
    }
    grid.innerHTML = tmdbState.items.map(tmdbCardHtml).join('');
    tmdbState._renderedCount = tmdbState.items.length;
    return;
  }

  // 增量追加：仅渲染尚未绘制的卡片，保留现有内容与选中态
  if(!tmdbState.items.length) return;
  var start = tmdbState._renderedCount || 0;
  if(start >= tmdbState.items.length) return;
  var html = tmdbState.items.slice(start).map(tmdbCardHtml).join('');
  grid.insertAdjacentHTML('beforeend', html);
  tmdbState._renderedCount = tmdbState.items.length;
}

function toggleTmdbCard(el, id){
  if(tmdbState.selected[id]){
    delete tmdbState.selected[id];
    el.classList.remove('selected');
    el.setAttribute('aria-pressed','false');
  }else{
    // 存储完整信息，以便翻页后仍能构建转存任务
    var item = null;
    for(var i=0;i<tmdbState.items.length;i++){
      if(tmdbState.items[i].id===id){item=tmdbState.items[i];break}
    }
    tmdbState.selected[id] = item ? {title: item.title} : {title: ''};
    el.classList.add('selected');
    el.setAttribute('aria-pressed','true');
  }
  updateTmdbSelBar();
}

// 全选本页可见资源（鼠标按钮 + 键盘 A）
function selectAllTmdbVisible(){
  tmdbState.items.forEach(function(item){
    tmdbState.selected[item.id] = {title: item.title};
  });
  document.querySelectorAll('#tmdbGrid .tmdb-card').forEach(function(c){
    c.classList.add('selected');
    c.setAttribute('aria-pressed','true');
  });
  updateTmdbSelBar();
  srAnnounce('已全选本页 ' + tmdbState.items.length + ' 项');
}

// 键盘导航：方向键移动焦点、空格/回车切换选中、A 全选、Esc 取消当前焦点
function onTmdbGridKeydown(e){
  var grid = document.getElementById('tmdbGrid');
  if(!grid) return;
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.tmdb-card'));
  if(!cards.length) return;
  // A 全选（输入框内不拦截）
  if(e.key === 'a' || e.key === 'A'){
    if(e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    e.preventDefault(); selectAllTmdbVisible(); return;
  }
  var active = document.activeElement;
  var idx = cards.indexOf(active);
  if(idx === -1){
    // 焦点不在卡片上，方向键先聚焦第一张
    if(e.key === 'ArrowDown' || e.key === 'ArrowRight' || e.key === 'ArrowUp' || e.key === 'ArrowLeft'){
      e.preventDefault(); cards[0].focus();
    }
    return;
  }
  // 计算当前列数（响应式会变化）
  var cols = 5;
  var cs = getComputedStyle(grid).gridTemplateColumns;
  if(cs){ var parts = cs.trim().split(/\s+/); if(parts.length > 1) cols = parts.length; }
  var next = idx;
  switch(e.key){
    case 'ArrowRight': next = idx + 1; break;
    case 'ArrowLeft':  next = idx - 1; break;
    case 'ArrowDown':  next = idx + cols; break;
    case 'ArrowUp':    next = idx - cols; break;
    case 'Home':       next = 0; break;
    case 'End':        next = cards.length - 1; break;
    case ' ':
    case 'Enter':
      e.preventDefault();
      toggleTmdbCard(active, parseInt(active.dataset.id, 10));
      return;
    default: return;
  }
  if(next < 0 || next >= cards.length) return;
  e.preventDefault();
  cards[next].focus();
}

function updateTmdbSelBar(){
  var count = Object.keys(tmdbState.selected).length;
  document.getElementById('tmdbSelCount').textContent = '已选 '+count+' 部';
  var active = count > 0;
  document.getElementById('tmdbSelBar').classList.toggle('active', active);
  // 选中栏为 fixed 浮层，会遮挡底部内容（含分页按钮）；
  // 选中时给页面预留底部空间，使「下一页」等可正常点击
  document.getElementById('pageTmdb').classList.toggle('sel-active', active);
}

function clearTmdbSelection(){
  tmdbState.selected = {};
  document.querySelectorAll('.tmdb-card.selected').forEach(function(c){c.classList.remove('selected')});
  updateTmdbSelBar();
}

async function transferTmdbSelection(){
  var ids = Object.keys(tmdbState.selected);
  if(!ids.length){showToast('请先选择内容',false);return}
  // 构建转存任务 - 直接从 selected 中获取信息，不依赖当前页 items
  var mt = document.getElementById('tmdbMediaType').value;
  var category = mt === 'movie' ? 'movie' : 'tv';
  var savepath = APP_PATHS.tmdb||'/批量转存/TMDB';
  var tasks = [];
  for(var i=0; i<ids.length; i++){
    var sel = tmdbState.selected[ids[i]];
    if(sel){
      tasks.push({
        path: 'tmdb',
        type: category,
        savepath: savepath,
        _wish: true,
        title: sel.title || ('TMDB_'+ids[i]),
        category: category
      });
    }
  }
  if(!tasks.length){showToast('未找到选中项',false);return}
  // 清空日志（日志面板常驻，无需切换页面即可看到转存进度）
  logBefore=[]; document.getElementById('log').textContent='';
  addLog('[开始转存 TMDB 选中内容]');
  try{
    var d = await apiPost('/api/transfer',{tasks:tasks,limit:5,filters:{}});
    if(d.success){
      showToast('已提交 '+tasks.length+' 项转存',true);
      document.getElementById('stopBtn').style.display='inline-block';
      startLogPoll(3000);
      clearTmdbSelection();
    }else{
      if(d.conflict){
        showToast('已有转存任务在运行',false);
      }else{
        showToast(d.message||'转存失败',false);
      }
    }
  }catch(e){showToast('请求失败: '+e.message,false)}
}

function renderTmdbPagination(){
  var pg = document.getElementById('tmdbPagination');
  if(tmdbState.total_pages <= 1){
    pg.style.display = 'none';
    return;
  }
  pg.style.display = 'flex';
  document.getElementById('tmdbPageInfo').textContent = tmdbState.page + ' / ' + tmdbState.total_pages;
  document.getElementById('tmdbPrevBtn').disabled = tmdbState.page <= 1;
  document.getElementById('tmdbNextBtn').disabled = tmdbState.page >= tmdbState.total_pages;
}

function tmdbPrevPage(){
  if(tmdbState.page > 1){
    tmdbState.page--;
    loadTmdbList();
    window.scrollTo({top:0,behavior:'smooth'});
  }
}

function tmdbNextPage(){
  if(tmdbState.page < tmdbState.total_pages){
    tmdbState.page++;
    loadTmdbList();
    window.scrollTo({top:0,behavior:'smooth'});
  }
}

async function refreshTmdbCache(){
  try{
    var d = await apiGet('/api/tmdb/refresh');
    showToast(d.message||'已刷新',true);
    tmdbState.page = 1;
    await loadTmdbList();
  }catch(e){showToast('刷新失败',false)}
}

// ============ 无限滚动 ============

// 判断 #tmdbGrid 底部是否已接近可视区底部（阈值 400px）
function tmdbNearBottom(){
  var grid = document.getElementById('tmdbGrid');
  if(!grid) return false;
  // 真正的滚动容器是 .content（body 设了 overflow:hidden，window 不滚动）
  var sc = document.querySelector('.content');
  var viewportBottom = sc ? sc.getBoundingClientRect().bottom : window.innerHeight;
  var gridRect = grid.getBoundingClientRect();
  return (viewportBottom - gridRect.bottom) <= 400;
}

// 滚动回调：仅在 TMDB 标签页可见且未到底时追加下一页
function tmdbOnScroll(){
  if(!tmdbState.initialized) return;
  var page = document.getElementById('pageTmdb');
  if(!page || !page.classList.contains('active')) return;
  if(tmdbState.loadingMore || tmdbState.allLoaded) return;
  if(!tmdbNearBottom()) return;
  loadTmdbList(true);
}

// 绑定滚动监听（只绑定一次）。滚动容器优先取 .content，否则回退到 window。
function bindTmdbScroll(){
  if(tmdbState._scrollBound) return;
  var sc = document.querySelector('.content') || window;
  sc.addEventListener('scroll', tmdbOnScroll, {passive:true});
  tmdbState._scrollBound = true;
}


/**
 * 折叠/展开 TMDB 筛选栏（UX #3：降低浏览页首屏认知负荷）。
 */
function toggleTmdbFilters(){
  var wrap = document.getElementById('tmdbFilters');
  var btn = document.getElementById('tmdbFilterToggle');
  if(!wrap) return;
  var collapsed = wrap.classList.toggle('collapsed');
  if(btn){
    btn.setAttribute('aria-expanded', String(!collapsed));
    btn.textContent = collapsed ? '\u5c55\u5f00\u7b5b\u9009' : '\u6536\u8d77\u7b5b\u9009';
  }
}
