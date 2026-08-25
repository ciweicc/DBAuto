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
if(s.savepaths){
  document.getElementById('cfg_path_category_base').value=s.savepaths.category_base||'/影视';
  document.getElementById('cfg_path_search').value=s.savepaths.search||'/批量转存/手动搜索存';
  document.getElementById('cfg_path_tmdb').value=s.savepaths.tmdb||'/批量转存/TMDB';
}

  }catch(e){showToast('加载配置失败',false)}
}
async function refreshDoubanCache(){
var btn=document.getElementById('refreshDoubanBtn');
if(btn)btn.classList.add('loading');
try{var d=await apiGet('/api/refresh_douban');showToast(d.message||'已刷新',true)}
catch(e){showToast('刷新失败',false)}
finally{if(btn)btn.classList.remove('loading')}
}
async function saveConfig(silent=false){
SETTINGS_ALL = null;  // 失效缓存，保存后 SSE 触发的 loadConfig 将拉取最新数据
// 3.6 前置校验：转存路径必须以 / 开头，输入时即时提示，不通过则阻断保存并聚焦首个错误项
var pathIds=['cfg_path_category_base','cfg_path_search','cfg_path_tmdb'];
var firstBad=null;
pathIds.forEach(function(id){
  var el=document.getElementById(id);
  if(!el)return;
  var bad=el.value.trim().length>0&&el.value.trim().charAt(0)!=='/';
  el.classList.toggle('input-error',bad);
  el.title=bad?'路径必须以 / 开头':'';
  if(bad&&!firstBad)firstBad=el;
});
if(firstBad){showToast('转存路径必须以 / 开头',false);firstBad.focus();return}
var saveBtn=document.getElementById('saveSettingsBtn');
if(saveBtn)saveBtn.classList.add('loading');
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
// 保存转存路径设置（豆瓣想看同步已移除，仅保留 savepaths）
var savepaths={category_base:document.getElementById('cfg_path_category_base').value.trim()||'/影视',
             search:document.getElementById('cfg_path_search').value.trim()||'/批量转存/手动搜索存',
             tmdb:document.getElementById('cfg_path_tmdb').value.trim()||'/批量转存/TMDB'};
await apiPost('/api/schedule',{action:'save',savepaths:savepaths});
APP_PATHS.category_base=savepaths.category_base;
APP_PATHS.search=savepaths.search;
APP_PATHS.tmdb=savepaths.tmdb;
}catch(e){showToast('保存失败',false)}
finally{if(saveBtn)saveBtn.classList.remove('loading')}
}

// 3.6 前置校验辅助：重新输入时即时清除错误态（本文件在 body 尾部拼接，DOM 已就绪）
['cfg_path_category_base','cfg_path_search','cfg_path_tmdb'].forEach(function(id){
  var el=document.getElementById(id);
  if(el)el.addEventListener('input',function(){el.classList.remove('input-error');el.title=''});
});

