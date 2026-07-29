// ============ Settings ============
function openSettings(){switchTab('settings')}
async function loadConfig(){
  try{var cfg=await apiGet('/api/config');
    document.getElementById('cfg_pansou').value=cfg.pansou||'';
    document.getElementById('cfg_qas').value=cfg.qas||'';
document.getElementById('cfg_qas_token').value='';
document.getElementById('cfg_auth_user').value=cfg.auth_user||'';
document.getElementById('cfg_auth_pass').value='';
document.getElementById('cfg_tmdb_api_key').value='';
document.getElementById('cfg_tmdb_base_url').value=cfg.tmdb_base_url||'';
// 加载想看同步设置
try{var s=await apiGet('/api/schedule');
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
}catch(e){}

  }catch(e){showToast('加载配置失败',false)}
}
async function refreshDoubanCache(){
try{var d=await apiGet('/api/refresh_douban');showToast(d.message||'已刷新',true)}
catch(e){showToast('刷新失败',false)}
}
async function saveConfig(silent=false){
var cfg={pansou:document.getElementById('cfg_pansou').value.trim(),
qas:document.getElementById('cfg_qas').value.trim(),
qas_token:document.getElementById('cfg_qas_token').value,
auth_user:document.getElementById('cfg_auth_user').value.trim(),
auth_pass:document.getElementById('cfg_auth_pass').value,
tmdb_api_key:document.getElementById('cfg_tmdb_api_key').value,
tmdb_base_url:document.getElementById('cfg_tmdb_base_url').value.trim()};
try{
var d=await apiPost('/api/config',cfg);
if(d.success){if(!silent)showToast('设置已保存',true)}
else showToast(d.message||'保存失败',false);
// 保存想看同步设置
var wishCats=[];document.querySelectorAll('#wishCategoryChips .chip.on').forEach(function(c){wishCats.push(c.dataset.cat)});
var wishAccounts=collectWishAccounts();
var wishCfg={action:'save',douban_wish:{enabled:document.getElementById('cfg_wish_enabled_chip').classList.contains('on'),savepath:document.getElementById('cfg_wish_savepath').value.trim(),category:wishCats.length?wishCats:['movie'],accounts:wishAccounts}};
await apiPost('/api/schedule',wishCfg);
}catch(e){showToast('保存失败',false)}
}

