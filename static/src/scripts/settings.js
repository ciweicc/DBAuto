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
  clearSettingsDirty();
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
if(d.success){if(!silent)showToast('设置已保存',true);clearSettingsDirty()}
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


// ============ 设置页视觉优化辅助 ============
// 1) 分区索引：点击 chips 展开目标分组并滚动定位
//    设置页约 3 屏高，此前唯一的分组默认展开、无定位入口，回看某项需长距离滚动。
function jumpToSettingsGroup(id, chip){
  var group = document.getElementById(id);
  if(!group) return;
  if(group.tagName === 'DETAILS') group.open = true;
  var target = group.querySelector('.settings-grid') || group;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  try{
    target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }catch(e){
    target.scrollIntoView();
  }
  // 挂起滚动监听，避免平滑滚动途中的中间分组抢走高亮
  suspendSettingsJumpSpy(reduce ? 200 : 900);
  // 焦点跟随：键盘用户跳转后可直接操作该分区首个输入框
  var firstInput = target.querySelector('input, textarea, select, button');
  if(firstInput && !reduce) setTimeout(function(){ try{ firstInput.focus({ preventScroll: true }); }catch(e){} }, 320);
  setSettingsJumpActive(chip);
}
function setSettingsJumpActive(chip){
  document.querySelectorAll('.set-jump-chip').forEach(function(c){
    c.classList.toggle('active', c === chip);
    if(c === chip) c.setAttribute('aria-current', 'true'); else c.removeAttribute('aria-current');
  });
}
// 滚动时高亮当前可见分组对应的 chip（IntersectionObserver 不可用时静默降级）
//
// 注意：点击 chip 触发的平滑滚动会持续产生交叉回调，若不挂起 spy，
// 回调会用滚动途中的中间分组覆盖点击选中的 chip（表现为「点转存路径、
// 高亮却停在转存服务」）。因此跳转期间挂起 spy，滚动结束再恢复。
var _setJumpSpy = null;
var _setJumpSpySuspendUntil = 0;
function _setJumpSpyPaused(){
  return Date.now() < _setJumpSpySuspendUntil;
}
function suspendSettingsJumpSpy(ms){
  _setJumpSpySuspendUntil = Date.now() + (ms || 900);
}
function initSettingsJumpSpy(){
  if(typeof IntersectionObserver === 'undefined') return;
  var chips = Array.prototype.slice.call(document.querySelectorAll('.set-jump-chip'));
  if(!chips.length) return;
  var byId = {};
  chips.forEach(function(c){ byId[c.getAttribute('data-target')] = c; });
  var groups = Object.keys(byId).map(function(id){ return document.getElementById(id); }).filter(Boolean);
  if(!groups.length) return;
  _setJumpSpy = new IntersectionObserver(function(entries){
    if(_setJumpSpyPaused()) return;
    var visible = entries.filter(function(e){ return e.isIntersecting; })
      .sort(function(a, b){ return a.boundingClientRect.top - b.boundingClientRect.top; });
    var top = visible[0] || entries[entries.length - 1];
    if(top && byId[top.target.id]) setSettingsJumpActive(byId[top.target.id]);
  }, { root: document.querySelector('.content') || null, rootMargin: '-10% 0px -70% 0px', threshold: 0 });
  groups.forEach(function(g){ _setJumpSpy.observe(g); });
}

// 2) 保存条「有未保存改动」提示：让保存动作与改动建立可见关联
//    （此前保存按钮是页面最底部的一张空卡，与上方表单在视觉上完全脱节）
var SETTINGS_DIRTY_HINT = '有未保存的改动，点击右侧按钮写入';
function markSettingsDirty(){
  var el = document.getElementById('settingsDirtyHint');
  if(!el) return;
  el.textContent = SETTINGS_DIRTY_HINT;
  el.classList.add('dirty');
}
function clearSettingsDirty(){
  var el = document.getElementById('settingsDirtyHint');
  if(!el) return;
  el.textContent = '改动会同时写入配置与转存路径';
  el.classList.remove('dirty');
}
function initSettingsDirty(){
  var root = document.getElementById('tabSettings');
  if(!root) return;
  root.addEventListener('input', function(e){
    var t = e.target;
    if(!t || (t.tagName !== 'INPUT' && t.tagName !== 'TEXTAREA' && t.tagName !== 'SELECT')) return;
    markSettingsDirty();
  });
  root.addEventListener('change', function(e){
    var t = e.target;
    if(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) markSettingsDirty();
  });
}

// 本文件在 body 尾部拼接，DOM 已就绪
initSettingsJumpSpy();
initSettingsDirty();
