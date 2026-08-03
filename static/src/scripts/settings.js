// ============ Settings ============
function openSettings(){switchTab('settings')}
async function loadConfig(){
  try{
    var all = SETTINGS_ALL || (SETTINGS_ALL = await apiGet('/api/settings/all'));
    var cfg = all.config || {};
    var s = all.schedule || {};
    document.getElementById('cfg_pansou').value=cfg.pansou||'';
    document.getElementById('cfg_qas').value=cfg.qas||'';
document.getElementById('cfg_qas_token').value='';
document.getElementById('cfg_auth_user').value=cfg.auth_user||'';
document.getElementById('cfg_auth_pass').value='';
document.getElementById('cfg_tmdb_api_key').value='';
document.getElementById('cfg_tmdb_base_url').value=cfg.tmdb_base_url||'';
document.getElementById('cfg_tmdb_proxy').value=cfg.tmdb_proxy||'';
// 加载想看同步设置
if(s.savepaths){
  document.getElementById('cfg_path_category_base').value=s.savepaths.category_base||'/影视';
  document.getElementById('cfg_path_search').value=s.savepaths.search||'/批量转存/手动搜索存';
  document.getElementById('cfg_path_tmdb').value=s.savepaths.tmdb||'/批量转存/TMDB';
}
if(s.douban_wish){
document.getElementById('cfg_wish_savepath').value=s.douban_wish.savepath||'/批量转存/想看';
var wishCats=s.douban_wish.category;
if(typeof wishCats==='string')wishCats=[wishCats];
if(!Array.isArray(wishCats))wishCats=['movie'];
document.querySelectorAll('#wishCategoryChips .chip').forEach(function(c){c.classList.toggle('on',wishCats.indexOf(c.dataset.cat)!==-1)});
document.getElementById('cfg_wish_enabled_chip').classList.toggle('on',!!s.douban_wish.enabled);
var wishAccounts=s.douban_wish.accounts||[];
if(!wishAccounts.length&&cfg.douban_uid){wishAccounts=[{uid:cfg.douban_uid,cookie:'',name:''}]}
renderWishAccounts(wishAccounts);
}

  }catch(e){showToast('加载配置失败',false)}
}
async function refreshDoubanCache(){
try{var d=await apiGet('/api/refresh_douban');showToast(d.message||'已刷新',true)}
catch(e){showToast('刷新失败',false)}
}
async function saveConfig(silent=false){
SETTINGS_ALL = null;  // 失效缓存，保存后 SSE 触发的 loadConfig 将拉取最新数据
var cfg={pansou:document.getElementById('cfg_pansou').value.trim(),
qas:document.getElementById('cfg_qas').value.trim(),
qas_token:document.getElementById('cfg_qas_token').value,
auth_user:document.getElementById('cfg_auth_user').value.trim(),
auth_pass:document.getElementById('cfg_auth_pass').value,
tmdb_api_key:document.getElementById('cfg_tmdb_api_key').value,
tmdb_base_url:document.getElementById('cfg_tmdb_base_url').value.trim(),
tmdb_proxy:document.getElementById('cfg_tmdb_proxy').value.trim()};
try{
var d=await apiPost('/api/config',cfg);
if(d.success){if(!silent)showToast('设置已保存',true)}
else showToast(d.message||'保存失败',false);
// 保存想看同步设置
var wishCats=[];document.querySelectorAll('#wishCategoryChips .chip.on').forEach(function(c){wishCats.push(c.dataset.cat)});
var wishAccounts=collectWishAccounts();
var savepaths={category_base:document.getElementById('cfg_path_category_base').value.trim()||'/影视',
             search:document.getElementById('cfg_path_search').value.trim()||'/批量转存/手动搜索存',
             tmdb:document.getElementById('cfg_path_tmdb').value.trim()||'/批量转存/TMDB'};
var wishCfg={action:'save',douban_wish:{enabled:document.getElementById('cfg_wish_enabled_chip').classList.contains('on'),savepath:document.getElementById('cfg_wish_savepath').value.trim(),category:wishCats.length?wishCats:['movie'],accounts:wishAccounts},savepaths:savepaths};
await apiPost('/api/schedule',wishCfg);
APP_PATHS.category_base=savepaths.category_base;
APP_PATHS.search=savepaths.search;
APP_PATHS.tmdb=savepaths.tmdb;
}catch(e){showToast('保存失败',false)}
}

