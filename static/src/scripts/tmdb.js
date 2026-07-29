// ============ TMDB Page ============
var tmdbState = {
  page: 1,
  total_pages: 1,
  media_type: 'movie',
  list_type: 'trending',
  genre_id: 0,
  items: [],
  selected: {},
  options: null,
  genres: [],
  initialized: false
};

async function initTmdbPage(){
  if(!tmdbState.initialized){
    // 加载选项
    try{
      var opts = await apiGet('/api/tmdb/options');
      tmdbState.options = opts;
      // 填充地区
      var regionSel = document.getElementById('tmdbRegion');
      regionSel.innerHTML = opts.regions.map(function(r){return '<option value="'+r.code+'">'+r.name+'</option>'}).join('');
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
    var html = '<span class="chip on" data-gid="0" onclick="toggleTmdbGenre(this)">全部</span>';
    html += tmdbState.genres.map(function(g){return '<span class="chip" data-gid="'+g.id+'" onclick="toggleTmdbGenre(this)">'+g.name+'</span>'}).join('');
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

async function loadTmdbList(){
  var mt = document.getElementById('tmdbMediaType').value;
  var lt = document.getElementById('tmdbListType').value;
  var region = document.getElementById('tmdbRegion').value;
  var sort = document.getElementById('tmdbSort').value;
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

  var grid = document.getElementById('tmdbGrid');
  grid.innerHTML = '<div class="tmdb-loading" style="grid-column:1/-1"><div class="spinner"></div> 加载中...</div>';

  var params = 'media_type='+mt+'&list_type='+lt+'&page='+tmdbState.page;
  if(lt === 'discover'){
    if(genreId) params += '&genre_id='+genreId;
    if(year) params += '&year='+year;
    if(minRating) params += '&min_rating='+minRating;
    params += '&sort_by='+sort;
  }
  if(region) params += '&region='+region;

  try{
    var d = await apiGet('/api/tmdb/list?'+params);
    tmdbState.items = d.items || [];
    tmdbState.total_pages = d.total_pages || 1;
    renderTmdbGrid();
    renderTmdbPagination();
    if(d.error){
      grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">⚠ '+esc(d.error)+'</div>';
    }
  }catch(e){
    grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">加载失败: '+esc(e.message||'')+'<br><span style="font-size:12px;opacity:.7">请检查 TMDB API Key 是否正确，以及网络是否能访问 themoviedb.org</span></div>';
  }
}

function renderTmdbGrid(){
  var grid = document.getElementById('tmdbGrid');
  if(!tmdbState.items.length){
    grid.innerHTML = '<div class="tmdb-empty" style="grid-column:1/-1">暂无数据</div>';
    return;
  }
  grid.innerHTML = tmdbState.items.map(function(item){
    var sel = tmdbState.selected[item.id] ? ' selected' : '';
    var poster = item.poster || '';
    var posterHtml = poster
      ? '<img class="tmdb-poster" src="'+esc(poster)+'" loading="lazy" alt="'+esc(item.title)+'">'
      : '<div class="tmdb-poster" style="display:flex;align-items:center;justify-content:center;font-size:40px;color:var(--text3)">🎬</div>';
    var ratingHtml = item.rating > 0
      ? '<div class="tmdb-rating-badge">★ '+item.rating.toFixed(1)+'</div>'
      : '';
    var yearStr = item.year ? item.year : '';
    var votesStr = item.votes > 0 ? (item.votes >= 1000 ? (item.votes/1000).toFixed(1)+'k' : item.votes) + '票' : '';
    var metaParts = [];
    if(yearStr) metaParts.push(yearStr);
    if(votesStr) metaParts.push(votesStr);
    return '<div class="tmdb-card'+sel+'" onclick="toggleTmdbCard(this,'+item.id+')">'+
      '<div class="tmdb-poster-wrap">'+posterHtml+ratingHtml+'</div>'+
      '<div class="tmdb-info">'+
        '<div class="tmdb-title">'+esc(item.title)+'</div>'+
        '<div class="tmdb-meta">'+metaParts.join(' · ')+'</div>'+
        (item.overview ? '<div class="tmdb-overview">'+esc(item.overview)+'</div>' : '')+
      '</div>'+
    '</div>';
  }).join('');
}

function toggleTmdbCard(el, id){
  if(tmdbState.selected[id]){
    delete tmdbState.selected[id];
    el.classList.remove('selected');
  }else{
    // 存储完整信息，以便翻页后仍能构建转存任务
    var item = null;
    for(var i=0;i<tmdbState.items.length;i++){
      if(tmdbState.items[i].id===id){item=tmdbState.items[i];break}
    }
    tmdbState.selected[id] = item ? {title: item.title} : {title: ''};
    el.classList.add('selected');
  }
  updateTmdbSelBar();
}

function updateTmdbSelBar(){
  var count = Object.keys(tmdbState.selected).length;
  document.getElementById('tmdbSelCount').textContent = '已选 '+count+' 部';
  document.getElementById('tmdbSelBar').classList.toggle('active', count > 0);
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
  // 提前清空日志并切换 tab，避免 SSE 早期日志被竞态清除
  switchTab('manual');
  document.getElementById('logCard').style.display='block';
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

