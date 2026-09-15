// ============ 发现区 / TMDB（概览页内的唯一一块） ============
//
// 设计结论（issue #14）：TMDB **不再是独立页面**，而是概览页里的「发现」分区。
// 用户诉求是「把 TMDB 页面的完整筛选栏原版移植到概览页，少一个页面」，
// 因此筛选栏 / 多选 / 转存 / 分页都只有一份 DOM，直接住在本分区里。
//
// 为了让同一套 ID（#tmdbGrid、#tmdbFilters…）能在概览页里工作，本模块通过
// getId()/query()/queryAll() 访问 DOM（而不是每个调用点各写一次 getElementById）：
//   - 「TMDB 自己的」控件（前缀 tmdb*）在 #tmdbPanel 作用域内解析，
//     这样这类通用名（#tmdbSort、#tmdbYear…）永远不会被别处的同名节点抢走；
//   - 「页面级」的节点（#log 日志区、#stopBtn…）仍按全局 id 解析 ——
//     它们不属于发现区，硬套作用域会在转存时拿到 null（曾因此在
//     `getId('log').textContent=''` 上抛 TypeError，转存直接中断）。
// 因此这里用**白名单前缀**而不是「所有 id 一律先查作用域」。
var DSC_HOST_ID = 'tmdbPanel';
// 页面级（全局）id：不属于发现区，必须按全局 id 解析
var DSC_GLOBAL_IDS = {log:1, stopBtn:1, logPanel:1, searchInput:1};
// 发现区标题行上的快捷入口：id 形如 ovTmdbFilterToggle（不在 #tmdbPanel 内部）
function _dscLocalId(id){
  if(DSC_GLOBAL_IDS[id]) return null;
  return 'ov' + id.charAt(0).toUpperCase() + id.slice(1);
}

function dscHost(){
  var host = document.getElementById(DSC_HOST_ID);
  return host || document;
}

/** 单节点查找：快捷入口 → 发现区作用域 → 全局兜底 */
function getId(id){
  var localId = _dscLocalId(id);
  if(localId){
    var local = document.getElementById(localId);
    if(local) return local;
  }
  var scoped = dscHost().querySelector('#' + id);
  if(scoped) return scoped;
  return document.getElementById(id);
}

/** 作用域内查询（选择器一律写在发现区内部） */
function query(sel){ return dscHost().querySelector(sel); }
function queryAll(sel){ return dscHost().querySelectorAll(sel); }

// 「概览页 → 发现区」的跳转入口（历史遗留的深链函数）。
//
// 旧版 TMDB 是独立 Tab，概览页的「TMDB 快照」用 openDiscovery(filters) 把筛选带过去；
// 现在只有一块界面，跳转退化为「滚到发现区并聚焦筛选栏」，
// 但为了不破坏既有调用点与 E2E 断言，函数与语义（幂等、可带 filters）都保留。
function openDiscovery(filters){
  if(typeof filters === 'object' && filters){
    var mtEl = getId('tmdbMediaType'), ltEl = getId('tmdbListType');
    if(mtEl && filters.media_type) mtEl.value = filters.media_type;
    if(ltEl && filters.list_type){
      var ok = [].some.call(ltEl.options, function(o){ return o.value === filters.list_type; });
      if(ok) ltEl.value = filters.list_type;
    }
    if(typeof filters.page === 'number' && filters.page > 0) dscState.page = filters.page;
  }
  if(typeof scrollTmdbPanelIntoView === 'function') scrollTmdbPanelIntoView();
  if(!dscState.initialized) initTmdbPage();
}

var dscState = {
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
  _backToTopCreated: false, // “回到顶部”按钮是否已创建（防止重复创建）
  _renderedCount: 0,    // 已渲染到 grid 的卡片数量（用于增量追加）
  lastScrollTrigger: 0, // 上次滚动触发加载的时间戳（节流用，单位 ms）
  lastAppendAt: 0,      // 上次“成功追加加载完成”的时间戳（追加后冷却用，单位 ms）
  entering: false,      // 是否正在回到发现区（此时保留当前筛选，不做「重置到第 1 页」）
  lastVisible: false    // 本块上一次的可见状态（用于从其它 Tab 切回时刷新回顶按钮）
};

// 无限滚动相关阈值常量（集中管理，便于调参）
var TMDB_SCROLL_THROTTLE_MS = 900;   // 滚动触发节流：两次触发的最小时间间隔
var TMDB_NEAR_BOTTOM_PX = 900;       // 接近底部的触发距离（越大越早预加载）
var TMDB_APPEND_COOLDOWN_MS = 1500;  // 一次成功追加加载完成后的最小冷却时间
var TMDB_BACK_TOP_THRESHOLD = 600;   // “回到顶部”按钮出现的滚动阈值（.content scrollTop，单位 px）

// 无封面占位卡片的 HTML：深色渐变背景 + 半透明大图标 + 居中的标题文字。
// 既保留“有信息可看”的观感，又契合现代流媒体（如 Netflix）的占位瓦片风格。
function tmdbNoPosterHtml(title){
  var safeTitle = esc(title || '');
  return '<div class="tmdb-poster tmdb-no-poster">'
    + '<svg class="tmdb-no-poster-icon" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#icon-movie"/></svg>'
    + '<span class="tmdb-no-poster-title">' + safeTitle + '</span>'
    + '</div>';
}

// 封面图加载失败时的兜底：用富占位替换 <img>，并尽量从卡片 DOM 中取出标题。
function tmdbPosterFallback(img){
  var title = '';
  try{
    var card = img.closest ? img.closest('.tmdb-card') : null;
    if(card){
      var tEl = card.querySelector('.tmdb-title');
      if(tEl) title = tEl.textContent || (tEl.innerText || '');
    }
  }catch(e){ /* 取不到标题就只显示图标 */ }
  img.outerHTML = tmdbNoPosterHtml(title);
}

async function initTmdbPage(){
  // 点击页面其它区域或按 Esc 关闭平台下拉（仅绑定一次）
  if(!dscState._outsideBound){
    document.addEventListener('click', function(e){
      if(e.key === 'Escape'){ closeProviderDropdown(); return; }
      var wrap = getId('tmdbProviderWrap');
      if(wrap && !wrap.contains(e.target)) closeProviderDropdown();
    });
    dscState._outsideBound = true;
  }
  if(!dscState.initialized){
    // 加载选项
    try{
      var opts = await apiGet('/api/tmdb/options');
      dscState.options = opts;
      // 填充地区（国家）与语言
      var regionSel = getId('tmdbRegion');
      // 后端 /api/tmdb/options 的字段是 regions（兼容更早的 countries 命名）
      var regionList = opts.regions || opts.countries || [];
      if(regionSel) regionSel.innerHTML = regionList.map(function(r){return '<option value="'+r.code+'">'+r.name+'</option>'}).join('');
      var langSel = getId('tmdbLanguage');
      // languages 同样做缺失兜底：桩数据/老接口只给 countries 或 regions 时，
      // 这里一个 undefined.map 会让整个初始化中断（首屏海报墙空着、筛选下拉全空）
      var langList = opts.languages || [];
      if(langSel) langSel.innerHTML = langList.map(function(l){return '<option value="'+l.code+'">'+l.name+'</option>'}).join('');
      // 填充列表类型
      updateTmdbListTypeOptions(opts.movie_list_types);
      dscState.initialized = true;
    }catch(e){
      console.error('TMDB init error', e);
    }
  }
  // 检查 API Key
  try{
    var cfg = await apiGet('/api/config');
    if(cfg.tmdb_api_key && cfg.tmdb_api_key !== '***'){
      getId('tmdbKeyWarning').style.display = 'none';
    }else if(cfg.tmdb_api_key === '***'){
      getId('tmdbKeyWarning').style.display = 'none';
    }else{
      // 只提示、不早退：早退会让海报墙/筛选栏停在空壳（桩数据与「刚打开还没配 key」
      // 这两种情况都会命中），而下面的列表请求本身就会给出可读的错误文案。
      getId('tmdbKeyWarning').style.display = 'flex';
    }
  }catch(e){}
  // 加载类型和列表
  await loadTmdbGenres();
  await loadTmdbList();
  // 绑定无限滚动监听（只绑定一次）
  bindTmdbScroll();
  // 创建“回到顶部”悬浮按钮（只创建一次）
  ensureTmdbBackToTop();
  updateTmdbVisibility();
}

// 进入发现区：把「用户看到的」与「数据是什么」对齐后刷新一次。
//
// 与旧版的差别：过去每次 switchTab('tmdb') 都会无脑 loadTmdbList()（回到第 1 页）；
// 现在 TMDB 常驻概览页，切回概览时也走这里 —— 若照旧重载，
// 用户滚回发现区会发现自己刚翻到的第 5 页凭空变回第 1 页。
// 因此：首次进入或筛选条件确实变化时才重载，否则只把版面滚回发现区顶部。
async function enterTmdbDiscovery(opts){
  opts = opts || {};
  if(dscState.entering) return;
  dscState.entering = true;
  try{
    var wasInitialized = dscState.initialized;
    if(!wasInitialized){
      await initTmdbPage();
    }else{
      var mtEl = getId('tmdbMediaType');
      var ltEl = getId('tmdbListType');
      var changed = !!(mtEl && mtEl.value !== dscState.media_type)
                 || !!(ltEl && ltEl.value !== dscState.list_type);
      if(changed) await loadTmdbList();
      if(opts.scroll !== false) scrollTmdbPanelIntoView();
    }
  }catch(e){
    console.error('TMDB enter error', e);
  }finally{
    dscState.entering = false;
  }
}

// 把发现区滚到可视区顶部（概览页的滚动容器是 .content）。
// 用滚动容器相对坐标而不是 getBoundingClientRect 的绝对底边，
// 避免首屏「概览工作区」很高时出现「算出来在下面、其实差一点」的抖动。
function scrollTmdbPanelIntoView(){
  var host = document.getElementById(DSC_HOST_ID);
  var sc = document.querySelector('.content');
  if(!host || !sc) return;
  var top = sc.scrollTop + host.getBoundingClientRect().top - sc.getBoundingClientRect().top - 12;
  try{ sc.scrollTo({ top: Math.max(0, top), behavior: 'smooth' }); }
  catch(e){ sc.scrollTop = Math.max(0, top); }
}

// 逐帧采样发现区是否与滚动容器相交（最长 ~320ms）。
// 为什么不用 scroll 事件：scrollTmdbPanelIntoView 用的是 behavior:'smooth'，
// 平滑滚动会在若干帧内完成，等一次 scroll 事件容易漏判；这里直接对位置采样，判定不依赖事件时序。
function whenTmdbPanelVisible(cb, tries){
  tries = (tries === undefined) ? 20 : tries;
  var host = document.getElementById(DSC_HOST_ID);
  var sc = document.querySelector('.content');
  if(!host || !sc){ cb(); return; }
  var hr = host.getBoundingClientRect();
  var sr = sc.getBoundingClientRect();
  if(hr.bottom > sr.top + 8 && hr.top < sr.bottom){ cb(); return; }
  if(tries <= 0) return;
  setTimeout(function(){ whenTmdbPanelVisible(cb, tries - 1); }, 16);
}

// 刷新「发现区的当前可见性」到状态位。
// 回顶按钮、无限滚动都要用它判断「用户现在看的是不是发现区」。
function updateTmdbVisibility(){
  var host = document.getElementById(DSC_HOST_ID);
  var sc = document.querySelector('.content');
  var overviewActive = false;
  var page = document.getElementById('pageOverview');
  if(page) overviewActive = page.classList.contains('active');
  var visible = false;
  if(host && sc && overviewActive){
    var hr = host.getBoundingClientRect();
    var sr = sc.getBoundingClientRect();
    visible = hr.bottom > sr.top + 8 && hr.top < sr.bottom - 8;
  }
  dscState.lastVisible = visible;
  if(visible) whenTmdbPanelVisible(tmdbUpdateBackToTop, 1);
  else tmdbHideBackToTop();
  return visible;
}

// 当前显示的是否是发现区（渲染相关的回顶按钮据此显示）
function tmdbBlockVisible(){
  return !!dscState.lastVisible;
}

function updateTmdbListTypeOptions(listTypes){
  var sel = getId('tmdbListType');
  if(!sel) return;
  // 选项契约：{code,name}（后端 /api/tmdb/options 的 movie_list_types / tv_list_types）。
  // 缺字段的桩数据会渲染出 value="undefined" 的下拉项，会让后续请求带上
  // list_type=undefined（后端取不到榜单 → 空网格），因此这里做字段兜底。
  var current = sel.value || 'trending';
  sel.innerHTML = (listTypes || []).map(function(l){
    var code = (l && (l.code != null ? l.code : l.id)) || '';
    var name = (l && (l.name != null ? l.name : l.code)) || code;
    return '<option value="'+esc(code)+'">'+esc(name)+'</option>';
  }).join('');
  // 尝试保持当前选择
  if([].some.call(sel.options, function(o){return o.value === current})){
    sel.value = current;
  }
}

async function loadTmdbGenres(){
  var mt = getId('tmdbMediaType').value;
  if(mt === dscState.media_type && dscState.genres.length) return;
  dscState.media_type = mt;
  try{
    var d = await apiGet('/api/tmdb/genres?media_type='+mt);
    dscState.genres = d.genres || [];
    var chips = getId('tmdbGenreChips');
    var html = '<button type="button" class="chip on" data-gid="0" onclick="toggleTmdbGenre(this)">全部</button>';
    html += dscState.genres.map(function(g){return '<button type="button" class="chip" data-gid="'+g.id+'" onclick="toggleTmdbGenre(this)">'+g.name+'</button>'}).join('');
    chips.innerHTML = html;
    dscState.genre_id = 0;
  }catch(e){
    console.error('TMDB genres error', e);
  }
}

function toggleTmdbGenre(el){
  // 如果点击的条目已选中，无需重新加载
  if(el.classList.contains('on')) return;
  var chips = queryAll('#tmdbGenreChips .chip');
  chips.forEach(function(c){c.classList.remove('on')});
  el.classList.add('on');
  dscState.genre_id = parseInt(el.dataset.gid) || 0;
  dscState.page = 1;
  loadTmdbList();
}

async function loadTmdbProviders(){
  var mt = getId('tmdbMediaType').value;
  var country = getId('tmdbRegion').value || 'US';
  var panel = getId('tmdbProviderPanel');
  try{
    var d = await apiGet('/api/tmdb/providers?media_type='+mt+'&region='+country);
    var list = d.providers || [];
    dscState.providersList = list;
    // 地区/分类切换后平台集合变化，清空已选并重置锚点
    dscState.providers = [];
    dscState.providerAnchor = -1;
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
  var btn = getId('tmdbProviderBtn');
  if(btn.disabled) return;
  var panel = getId('tmdbProviderPanel');
  var open = panel.style.display === 'block';
  panel.style.display = open ? 'none' : 'block';
  btn.setAttribute('aria-expanded', open ? 'false' : 'true');
}

function closeProviderDropdown(){
  var panel = getId('tmdbProviderPanel');
  if(panel) panel.style.display = 'none';
  var btn = getId('tmdbProviderBtn');
  if(btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleProviderOption(el, ev){
  ev.stopPropagation();
  var idx = parseInt(el.dataset.idx, 10);
  var pid = parseInt(el.dataset.pid, 10);
  var selIdx = dscState.providers.indexOf(pid);
  if(ev.shiftKey && dscState.providerAnchor >= 0 && dscState.providerAnchor !== idx){
    // 以锚点为准，把锚点到当前项这一段统一设为“目标状态”
    var a = Math.min(dscState.providerAnchor, idx);
    var b = Math.max(dscState.providerAnchor, idx);
    var targetOn = selIdx < 0; // 点击项当前未选 -> 整段选中；已选 -> 整段取消
    for(var i=a;i<=b;i++){
      var op = dscState.providersList[i];
      if(!op) continue;
      var oi = dscState.providers.indexOf(op.id);
      if(targetOn && oi < 0) dscState.providers.push(op.id);
      else if(!targetOn && oi >= 0) dscState.providers.splice(oi, 1);
    }
  }else{
    // 普通 / Ctrl / Cmd 点击：切换单项并设为新锚点
    if(selIdx < 0) dscState.providers.push(pid);
    else dscState.providers.splice(selIdx, 1);
    dscState.providerAnchor = idx;
  }
  renderProviderSelection();
}

function clearProviderSelection(ev){
  if(ev) ev.stopPropagation();
  dscState.providers = [];
  dscState.providerAnchor = -1;
  renderProviderSelection();
}

function renderProviderSelection(){
  var options = queryAll('#tmdbProviderPanel .tmdb-ms-option');
  var selSet = {};
  dscState.providers.forEach(function(id){ selSet[id] = true; });
  options.forEach(function(o){
    var pid = parseInt(o.dataset.pid, 10);
    var on = !!selSet[pid];
    o.classList.toggle('on', on);
    o.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  updateProviderLabel();
  if(dscState.list_type === 'discover'){
    dscState.page = 1;
    loadTmdbList();
  }
}

function updateProviderLabel(){
  var btn = getId('tmdbProviderBtn');
  var label = getId('tmdbProviderLabel');
  if(!btn || !label) return;
  var n = dscState.providers.length;
  if(!n){
    label.textContent = '全部平台';
    btn.classList.remove('has-sel');
  }else{
    label.textContent = '已选 ' + n + ' 个平台';
    btn.classList.add('has-sel');
  }
}

// 同步「仅 discover 榜单有效」的控件可用态（排序 / 评分 / 年份 / 流媒体平台）。
function syncTmdbDiscoverControls(){
  var ltEl = getId('tmdbListType');
  var isDiscover = (ltEl ? ltEl.value : dscState.list_type) === 'discover';
  var sortEl = getId('tmdbSort'), minEl = getId('tmdbMinRating');
  var yearEl = getId('tmdbYear'), provBtn = getId('tmdbProviderBtn');
  if(sortEl) sortEl.disabled = !isDiscover;
  if(minEl) minEl.disabled = !isDiscover;
  if(yearEl) yearEl.disabled = !isDiscover;
  if(provBtn) provBtn.disabled = !isDiscover;
}

function onTmdbFilterChange(){
  if(!dscState.options) return; // OPT-45：防止 options 未初始化时访问 .movie_list_types 抛 TypeError
  var prevMt = dscState.media_type;
  var prevLt = dscState.list_type;
  // 回到发现区时的对齐：只同步「筛选控件可用态」，不重置页码、不重新拉取列表
  // （避免用户翻到第 5 页、切走再切回时列表被打回第 1 页）
  if(dscState.entering){
    syncTmdbDiscoverControls();
    return;
  }
  var mt = getId('tmdbMediaType').value;
  // 如果切换了 media_type，更新列表类型选项
  if(mt !== dscState.media_type){
    var listTypes = mt === 'movie' ? dscState.options.movie_list_types : dscState.options.tv_list_types;
    updateTmdbListTypeOptions(listTypes);
  }
  // 更新列表选项后重新获取 lt，确保使用最新的下拉框值
  var lt = getId('tmdbListType').value;
  // 如果用户手动从 discover 切换到其他榜单类型，清除 discover 专属筛选条件
  if(lt !== 'discover' && dscState.list_type === 'discover'){
    dscState.genre_id = 0;
    var chips = queryAll('#tmdbGenreChips .chip');
    chips.forEach(function(c){c.classList.remove('on')});
    var allChip = query('#tmdbGenreChips .chip[data-gid="0"]');
    if(allChip) allChip.classList.add('on');
    getId('tmdbMinRating').value = 0;
    getId('tmdbYear').value = '';
  }
  dscState.list_type = lt;
  syncTmdbDiscoverControls();
  var isDiscover = lt === 'discover';
  if(isDiscover){
    // 进入 discover 或地区/分类切换时，刷新当前地区可用的流媒体平台列表
    loadTmdbProviders();
  }else{
    clearProviderSelection();
    closeProviderDropdown();
  }
  // 切换 media_type 时重新加载类型
  // OPT-04：切换媒体类型或榜单类型后，之前跨列表选择（dscState.selected）指向旧列表的条目，
  // 若继续保留，转存时会混入无效 id。这里统一重置选择状态，保证选择栏与待转存任务一致。
  if(mt !== prevMt || lt !== prevLt){
    clearTmdbSelection();
  }
  if(mt !== dscState.media_type){
    dscState.media_type = mt;
    dscState.genres = [];
    loadTmdbGenres().then(function(){dscState.page = 1; loadTmdbList()});
  }else{
    dscState.page = 1;
    loadTmdbList();
  }
}

// 读取当前筛选条件并拼装 /api/tmdb/list 请求参数
function buildTmdbListParams(){
  var mt = getId('tmdbMediaType').value;
  var lt = getId('tmdbListType').value;
  var region = getId('tmdbRegion').value;
  var language = getId('tmdbLanguage') ? getId('tmdbLanguage').value : '';
  var sort = getId('tmdbSort').value;
  var minRating = parseFloat(getId('tmdbMinRating').value) || 0;
  var year = parseInt(getId('tmdbYear').value) || 0;
  var genreId = dscState.genre_id || 0;
  var provider = dscState.providers.join(',');

  var params = 'media_type='+(mt || 'movie')+'&list_type='+(lt || 'trending')+'&page='+dscState.page;
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
  var grid = getId('tmdbGrid');

  // ---- 增量加载（无限滚动）----
  if(append){
    // 防护：已在加载 / 已全部加载完 / 没有下一页 时直接返回
    if(dscState.loadingMore || dscState.allLoaded || dscState.page >= dscState.total_pages) return;
    // 关键：在发起异步请求之前就同步置位 loadingMore，
    // 保证本函数返回（遇到 await）前守卫已生效，杜绝重复触发竞态。
    dscState.loadingMore = true;
    // 底部“加载更多”指示器（不清除现有 grid）
    grid.insertAdjacentHTML('beforeend',
      '<div id="tmdbLoadMore" style="grid-column:1/-1;display:flex;align-items:center;justify-content:center;gap:10px;padding:18px;color:var(--text3);font-size:13px">加载更多…</div>');
    var aparams = buildTmdbListParams();
    try{
      var ad = await apiGet('/api/tmdb/list?'+aparams);
      dscState.page++; // OPT-27：成功拿到数据后再前进页码，避免失败页被永久跳过
      var newItems = ad.items || [];
      // 按 id 去重（切换筛选条件时不同页可能重叠）
      var seen = {};
      dscState.items.forEach(function(it){ seen[it.id] = true; });
      var deduped = newItems.filter(function(it){
        if(seen[it.id]) return false;
        seen[it.id] = true;
        return true;
      });
      dscState.items = dscState.items.concat(deduped);
      if(ad.total_pages) dscState.total_pages = ad.total_pages;
      var alm = getId('tmdbLoadMore');
      if(alm) alm.remove();
      renderTmdbGrid(true);
      renderTmdbPagination();
      if(dscState.page >= dscState.total_pages) dscState.allLoaded = true;
      // 记录成功追加完成的时间，供滚动冷却使用
      dscState.lastAppendAt = Date.now();
    }catch(e){
      // 追加失败时仅停止并隐藏指示器，不清除已渲染内容
      var elm = getId('tmdbLoadMore');
      if(elm) elm.remove();
      console.error('TMDB append load error', e);
    }finally{
      // 无论成功失败都释放守卫，确保不会卡死在“加载中”
      dscState.loadingMore = false;
    }
    return;
  }

  // ---- 全新加载（替换）----
  dscState.items = [];
  dscState.allLoaded = false;
  dscState.loadingMore = false;
  dscState.lastAppendAt = 0; // 清空追加冷却，允许新列表立即响应滚动加载

  var mt = getId('tmdbMediaType').value;
  var lt = getId('tmdbListType').value;
  var minRating = parseFloat(getId('tmdbMinRating').value) || 0;
  var year = parseInt(getId('tmdbYear').value) || 0;
  var genreId = dscState.genre_id || 0;

  // 如果不是 discover，但用户选了类型/评分/年份，自动切换到 discover
  if(lt !== 'discover' && (genreId > 0 || minRating > 0 || year > 0)){
    getId('tmdbListType').value = 'discover';
    lt = 'discover';
    dscState.list_type = 'discover';
    // OPT-45：早返回前先清空旧网格，避免残留上一次列表内容
    grid.innerHTML = '<div class="tmdb-skeleton" style="grid-column:1/-1"></div>';
    onTmdbFilterChange();
    return;
  }

  var sk=''; for(var si=0;si<12;si++){ sk += '<div class="tmdb-skeleton"></div>'; }
  grid.innerHTML = sk;

  var params = buildTmdbListParams();

  try{
    var d = await apiGet('/api/tmdb/list?'+params);
    dscState.items = d.items || [];
    dscState.total_pages = d.total_pages || 1;
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
  // 全新加载后刷新“回到顶部”按钮状态（用户可能已滚动很深后切换筛选/分页）
  tmdbUpdateBackToTop();
}

// 单张卡片 HTML（供全新渲染与增量追加复用）
function tmdbCardHtml(item){
  var sel = dscState.selected[item.id] ? ' selected' : '';
  var poster = item.poster || '';
  var posterHtml = poster
    ? '<img class="tmdb-poster" src="'+esc(poster)+'" loading="lazy" decoding="async" alt="'+esc(item.title)+'" draggable="false" ondragstart="return false" onerror="tmdbPosterFallback(this)">'
    : tmdbNoPosterHtml(item.title);
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
  var grid = getId('tmdbGrid');

  // 全新渲染
  if(!append){
    if(!dscState.items.length){
      var es = renderEmptyState({icon:'icon-film', title:'暂无数据'});
      es.style.gridColumn = '1/-1';
      grid.textContent = '';
      grid.appendChild(es);
      dscState._renderedCount = 0;
      return;
    }
    grid.innerHTML = dscState.items.map(tmdbCardHtml).join('');
    dscState._renderedCount = dscState.items.length;
    return;
  }

  // 增量追加：仅渲染尚未绘制的卡片，保留现有内容与选中态
  if(!dscState.items.length) return;
  var start = dscState._renderedCount || 0;
  if(start >= dscState.items.length) return;
  var html = dscState.items.slice(start).map(tmdbCardHtml).join('');
  grid.insertAdjacentHTML('beforeend', html);
  dscState._renderedCount = dscState.items.length;
}

function toggleTmdbCard(el, id){
  if(dscState.selected[id]){
    delete dscState.selected[id];
    el.classList.remove('selected');
    el.setAttribute('aria-pressed','false');
  }else{
    // 存储完整信息，以便翻页后仍能构建转存任务
    var item = null;
    for(var i=0;i<dscState.items.length;i++){
      if(dscState.items[i].id===id){item=dscState.items[i];break}
    }
    dscState.selected[id] = item ? {title: item.title} : {title: ''};
    el.classList.add('selected');
    el.setAttribute('aria-pressed','true');
  }
  updateTmdbSelBar();
}

// 全选本页可见资源（鼠标按钮 + 键盘 A）
function selectAllTmdbVisible(){
  dscState.items.forEach(function(item){
    dscState.selected[item.id] = {title: item.title};
  });
  queryAll('#tmdbGrid .tmdb-card').forEach(function(c){
    c.classList.add('selected');
    c.setAttribute('aria-pressed','true');
  });
  updateTmdbSelBar();
  srAnnounce('已全选本页 ' + dscState.items.length + ' 项');
}

// 键盘导航：方向键移动焦点、空格/回车切换选中、A 全选、Esc 取消当前焦点
function onTmdbGridKeydown(e){
  var grid = getId('tmdbGrid');
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
  var count = Object.keys(dscState.selected).length;
  getId('tmdbSelCount').textContent = '已选 '+count+' 部';
  var active = count > 0;
  getId('tmdbSelBar').classList.toggle('active', active);
  // 选中栏为 fixed 浮层，会遮挡底部内容（含分页按钮）；
  // 选中时给页面预留底部空间，使「下一页」等可正常点击
  var host = document.getElementById(DSC_HOST_ID);
  if(host) host.classList.toggle('sel-active', active);
}

function clearTmdbSelection(){
  dscState.selected = {};
  queryAll('.tmdb-card.selected').forEach(function(c){c.classList.remove('selected')});
  updateTmdbSelBar();
}

async function transferTmdbSelection(){
  var ids = Object.keys(dscState.selected);
  if(!ids.length){showToast('请先选择内容',false);return}
  // 构建转存任务 - 直接从 selected 中获取信息，不依赖当前页 items
  var mt = getId('tmdbMediaType').value;
  var category = mt === 'movie' ? 'movie' : 'tv';
  var savepath = APP_PATHS.tmdb||'/批量转存/TMDB';
  var tasks = [];
  for(var i=0; i<ids.length; i++){
    var sel = dscState.selected[ids[i]];
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
  logBefore=[]; getId('log').textContent='';
  addLog('[开始转存 TMDB 选中内容]');
  // 3.1 统一 loading 态：异步期间禁点防重复提交
  var tBtn=getId('tmdbTransferBtn');
  if(tBtn)tBtn.classList.add('loading');
  try{
    var d = await apiPost('/api/transfer',{tasks:tasks,limit:5,filters:{}});
    if(d.success){
      showToast('已提交 '+tasks.length+' 项转存',true);
      getId('stopBtn').style.display='inline-block';
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
  finally{if(tBtn)tBtn.classList.remove('loading')}
}

function renderTmdbPagination(){
  var pg = getId('tmdbPagination');
  if(dscState.total_pages <= 1){
    pg.style.display = 'none';
    return;
  }
  pg.style.display = 'flex';
  getId('tmdbPageInfo').textContent = dscState.page + ' / ' + dscState.total_pages;
  getId('tmdbPrevBtn').disabled = dscState.page <= 1;
  getId('tmdbNextBtn').disabled = dscState.page >= dscState.total_pages;
}

function tmdbPrevPage(){
  if(dscState.page > 1){
    dscState.page--;
    // 翻页后回到发现区顶部（旧版是回到整页顶部，独立页面下两者等价；
    // 现在概览页首页很长，滚到整页顶部会把用户直接甩出发现区）
    scrollTmdbPanelIntoView();
    loadTmdbList();
  }
}

function tmdbNextPage(){
  if(dscState.page < dscState.total_pages){
    dscState.page++;
    scrollTmdbPanelIntoView();
    loadTmdbList();
  }
}

async function refreshTmdbCache(){
  try{
    var d = await apiGet('/api/tmdb/refresh');
    showToast(d.message||'已刷新',true);
    dscState.page = 1;
    await loadTmdbList();
  }catch(e){showToast('刷新失败',false)}
}

// ============ 无限滚动 ============

// 判断 #tmdbGrid 底部是否已接近可视区底部（阈值可配置，默认 900px）
function tmdbNearBottom(){
  var grid = getId('tmdbGrid');
  if(!grid) return false;
  // 真正的滚动容器是 .content（body 设了 overflow:hidden，window 不滚动）
  var sc = document.querySelector('.content');
  var viewportBottom = sc ? sc.getBoundingClientRect().bottom : window.innerHeight;
  var gridRect = grid.getBoundingClientRect();
  return (viewportBottom - gridRect.bottom) <= TMDB_NEAR_BOTTOM_PX;
}

// 滚动回调：仅在发现区真正可见、且未到底时追加下一页
// 通过“时间戳节流 + 追加后冷却”避免鼠标滚轮大跳变导致的连续重复加载。
function tmdbOnScroll(){
  if(!dscState.initialized) return;
  // 可见性 + 回顶按钮：与本函数下方「是否追加加载」的判定共用同一次测量，
  // 这样即使在 loadingMore / allLoaded / 冷却 等提前 return 的分支里，
  // 按钮状态也能实时跟随滚动更新。
  updateTmdbVisibility();
  if(!tmdbBlockVisible()) return;
  if(dscState.loadingMore || dscState.allLoaded) return;
  // 追加成功后冷却：避免“加载→新内容把旧内容顶上去→又触发滚动”的级联
  if(dscState.lastAppendAt && (Date.now() - dscState.lastAppendAt) < TMDB_APPEND_COOLDOWN_MS) return;
  // 时间戳节流：两次触发至少间隔 TMDB_SCROLL_THROTTLE_MS，轻量且与 passive 监听兼容
  var now = Date.now();
  if(now - dscState.lastScrollTrigger < TMDB_SCROLL_THROTTLE_MS) return;
  if(!tmdbNearBottom()) return;
  dscState.lastScrollTrigger = now;
  loadTmdbList(true);
}

// 绑定滚动监听（只绑定一次）。滚动容器优先取 .content，否则回退到 window。
function bindTmdbScroll(){
  if(dscState._scrollBound) return;
  var sc = document.querySelector('.content') || window;
  sc.addEventListener('scroll', tmdbOnScroll, {passive:true});
  dscState._scrollBound = true;
}

// ============ 一键回到顶部 ============

// 创建“回到顶部”悬浮按钮（只创建一次，guard 由 _backToTopCreated 标记 + DOM 存在性双重保证）。
// 按钮挂在 body 上（app 级），可见性由 TMDB 滚动位置 + 当前页是否激活共同驱动。
function ensureTmdbBackToTop(){
  if(dscState._backToTopCreated) return;
  var existing = getId('tmdbBackToTop');
  if(existing) return; // 防御：DOM 已存在则视为已创建，避免重复
  var btn = document.createElement('button');
  btn.id = 'tmdbBackToTop';
  btn.type = 'button';
  btn.className = 'tmdb-back-to-top';
  btn.setAttribute('aria-label', '回到顶部');
  btn.title = '回到顶部';
  // 上箭头图标（内联 SVG，随主题 currentColor 变化）
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
  // 点击：平滑滚动 .content 回到顶部（容器是 .content 而非 window）
  btn.addEventListener('click', function(){
    var content = document.querySelector('.content');
    if(content) content.scrollTo({ top: 0, behavior: 'smooth' });
  });
  document.body.appendChild(btn);
  dscState._backToTopCreated = true;
}

// 计算回顶按钮应避让的右侧距离，避免遮挡常驻日志面板（OPT-28）
function tmdbBackToTopRight(){
  var panel = getId('logPanel');
  if(!panel) return 24;
  // 移动端交还 CSS 媒体查询（right:16px + 上移）处理，不内联覆盖
  if(window.innerWidth <= 640) return null;
  var cs = getComputedStyle(panel);
  // 抽屉式（平板/移动）：默认收起在屏外，仅展开时避让
  if(cs.position === 'fixed'){
    if(!panel.classList.contains('open')) return 24;
    return panel.offsetWidth + 24;
  }
  // 桌面端常驻：按实际宽度（展开/折叠）避让
  return panel.offsetWidth + 24;
}

// 根据当前滚动位置与“发现区是否可见”计算按钮可见性，并切换 .visible 类。
// 不绑定独立滚动监听，由 tmdbOnScroll 复用同一 .content 滚动事件调用，避免重复触发。
function tmdbUpdateBackToTop(){
  if(!dscState._backToTopCreated) return;
  var btn = getId('tmdbBackToTop');
  if(!btn) return;
  var content = document.querySelector('.content');
  var host = document.getElementById(DSC_HOST_ID);
  if(!content || !host) return;
  var show = tmdbBlockVisible() && content.scrollTop > TMDB_BACK_TOP_THRESHOLD;
  btn.classList.toggle('visible', show);
  // OPT-28：桌面端按日志面板实际宽度右移，避免遮挡
  var right = tmdbBackToTopRight();
  btn.style.right = (right === null) ? '' : (right + 'px');
}

// 显式隐藏按钮（用于切换离开 TMDB 标签页时，避免悬浮按钮残留在其它页面）。
function tmdbHideBackToTop(){
  if(!dscState._backToTopCreated) return;
  var btn = getId('tmdbBackToTop');
  if(btn) btn.classList.remove('visible');
}


/**
 * 折叠/展开 TMDB 筛选栏（UX #3：降低浏览页首屏认知负荷）。
 */
function toggleTmdbFilters(){
  var wrap = getId('tmdbFilters');
  var btn = getId('tmdbFilterToggle');
  if(!wrap) return;
  var collapsed = wrap.classList.toggle('collapsed');
  if(btn){
    btn.setAttribute('aria-expanded', String(!collapsed));
    btn.textContent = collapsed ? '\u5c55\u5f00\u7b5b\u9009' : '\u6536\u8d77\u7b5b\u9009';
  }
  // 概览页把筛选栏做成可折叠的紧凑条（首屏信息密度），
  // 折叠时把内部控件的表单语义一并关闭：否则 Tab 键会依次走进一堆
  // 「看不见但可聚焦」的下拉框，键盘用户会凭空多按十几次。
  var adv = document.getElementById('tmdbAdv');
  if(adv) adv.disabled = collapsed;
}
