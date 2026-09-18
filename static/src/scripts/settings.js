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
// OpenList 媒体库数据源（issue #47：只剩令牌鉴权）：令牌留空不修改
var _olEl=document.getElementById('cfg_openlist'); if(_olEl)_olEl.value=cfg.openlist||'';
var _oltEl=document.getElementById('cfg_openlist_token'); if(_oltEl)_oltEl.value='';
var _olrEl=document.getElementById('cfg_openlist_root'); if(_olrEl)_olrEl.value=cfg.openlist_root||'';
// 折叠子分区的默认态由「有没有配过」决定：
//   未配置 → 展开（用户正是来配它的，收起等于把入口藏起来）；
//   已配置 → 收起（保持设置页高度门禁，issue #15）。
_applyOpenListSubState(cfg.openlist);
if(s.savepaths){
  document.getElementById('cfg_path_category_base').value=s.savepaths.category_base||'/影视';
  document.getElementById('cfg_path_search').value=s.savepaths.search||'/批量转存/手动搜索存';
  document.getElementById('cfg_path_tmdb').value=s.savepaths.tmdb||'/批量转存/TMDB';
  var lrEl=document.getElementById('cfg_path_library_root');
  if(lrEl) lrEl.value=s.savepaths.library_root||'/';
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
var pathIds=['cfg_path_category_base','cfg_path_search','cfg_path_tmdb','cfg_path_library_root'];
// OpenList 地址与 pansou/qas 同口径：可留空（留空=不用 OpenList），填了必须以 http(s):// 开头
var urlIds=['cfg_pansou','cfg_qas','cfg_openlist'];
var firstBadUrl=null;
urlIds.forEach(function(id){
  var el=document.getElementById(id);
  if(!el)return;
  var v=el.value.trim();
  var bad=v.length>0&&!/^https?:\/\//i.test(v);
  el.classList.toggle('input-error',bad);
  el.title=bad?'地址需以 http:// 或 https:// 开头':'';
  if(bad&&!firstBadUrl)firstBadUrl=el;
});
if(firstBadUrl){showToast('服务地址需以 http:// 或 https:// 开头',false);firstBadUrl.focus();return}
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
tmdb_proxy:document.getElementById('cfg_tmdb_proxy').value.trim(),
openlist:document.getElementById('cfg_openlist').value.trim(),
openlist_token:document.getElementById('cfg_openlist_token').value.trim(),
openlist_root:document.getElementById('cfg_openlist_root').value.trim()};
try{
var d=await apiPost('/api/config',cfg);
if(d.success){if(!silent)showToast('设置已保存',true);clearSettingsDirty()}
else showToast(d.message||'保存失败',false);
// 保存转存路径设置（豆瓣想看同步已移除，仅保留 savepaths）
var _lrEl=document.getElementById('cfg_path_library_root');
var savepaths={category_base:document.getElementById('cfg_path_category_base').value.trim()||'/影视',
             search:document.getElementById('cfg_path_search').value.trim()||'/批量转存/手动搜索存',
             tmdb:document.getElementById('cfg_path_tmdb').value.trim()||'/批量转存/TMDB',
             library_root:_lrEl&&_lrEl.value.trim()?(_lrEl.value.trim().replace(/\/+$/,'')||'/'):'/'};
await apiPost('/api/schedule',{action:'save',savepaths:savepaths});
APP_PATHS.category_base=savepaths.category_base;
APP_PATHS.search=savepaths.search;
APP_PATHS.tmdb=savepaths.tmdb;
APP_PATHS.library_root=savepaths.library_root;
}catch(e){showToast('保存失败',false)}
finally{if(saveBtn)saveBtn.classList.remove('loading')}
}

// 3.6 前置校验辅助：重新输入时即时清除错误态（本文件在 body 尾部拼接，DOM 已就绪）
['cfg_path_category_base','cfg_path_search','cfg_path_tmdb','cfg_path_library_root'].forEach(function(id){
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


// ============ OpenList 子分区状态（issue #27 追问） ============
// 折叠/展开只在「看」的层面省高度，语义上必须仍是一处正常的配置项：
// 未配置时默认展开，已配置时默认收起，徽标始终反映当前状态。
function _applyOpenListSubState(openlist){
  var d=document.getElementById('openlistSubGroup');
  var badge=document.getElementById('openlistSubBadge');
  if(d) d.open=!openlist;
  if(badge){
    badge.textContent=openlist?'已配置':'未配置';
    badge.className='set-sub-badge'+(openlist?' on':'');
  }
}

// ============ OpenList 连接自检（issue #27 追问） ============
// 为什么需要单独一个按钮：OpenList 配错的失败形态非常多（地址写错、端口不通、
// 令牌抄漏 / 已过期、只给了游客权限…），而它们**都表现为「媒体库读取失败」**。
// 若不在设置页就能直接验证，「配错了」和「OpenList 自己挂了」完全无法区分。
// 自检走的是后端现有的 /api/library/source，返回的是「谁在回答 + OpenList 是否可用 +
// 登录到的用户名」，不额外引入新接口。
// 自检结论要**留在页面上**（issue #47）：
// 之前只发一个几秒后自动消失的 toast，而结论文字落在折叠面板里的
// #openlistSelfCheckHint —— 面板默认收起时用户根本看不到原因，
// 只能看到「不可用」三个字，于是反复重填配置。
// 这里：失败时自动展开面板 + 给提示加状态样式（可被 DOM 走查脚本断言）。
function _renderOpenListHint(hint, msg, d){
  var grp=document.getElementById('openlistSubGroup');
  var ok=!!(d&&d.openlist_ok);
  if(hint){
    hint.textContent=msg;
    hint.className='form-hint ol-selfcheck'+(ok?' ol-ok':' ol-fail');
    hint.dataset.olKind=(d&&d.openlist_fail_kind)||'';
    hint.dataset.olStage=(d&&d.openlist_stage)||'';
  }
  if(grp&&!ok&&d&&d.openlist) grp.open=true;
}

async function checkOpenList(){
  var btn=document.getElementById('openlistCheckBtn');
  var hint=document.getElementById('openlistSelfCheckHint');
  if(hint)hint.textContent='自检中...';
  if(btn)btn.classList.add('loading');
  try{
    // 自检会**真的列一次目录**（后端 openlist.self_check）：
    // 只验证登录时，「挂载路径不匹配」会被误判成可用 —— 那正是
    // 「配了 OpenList 却仍回落」的成因，必须在这里就暴露出来。
    var d=await apiGet('/api/library/source');
    var msg;
    // 提示里必须同时出现「谁在回答」「用什么身份回答」「实测列到了什么」：
    // 只报「可用」，用户配了令牌也不知道到底生效没有（两者行为差异肉眼不可见）；
    // 只报「登录成功」，前缀填错时又会被误判成可用 —— 那正是
    // 「配了 OpenList 却仍回落」的成因，必须在这里就暴露出来。
    // 令牌这条路径还要点明「不会获得额外权限」—— 用户问的就是这个，
    // 若默认「令牌权限更大」，填了令牌却发现删不掉（403）时只会以为程序坏了。
    var mode=d.openlist_auth_mode||'';
    // 鉴权只剩两档（issue #47）：令牌 / 游客。旧档位的文案一并删掉 ——
    // 留着 'mixed'/'password' 分支等于在界面上承认「还有账号密码这条路」，
    // 而它已经不存在了，只会让用户去翻一个没有的输入框。
    var modeTxt=mode==='token'?'令牌鉴权':(mode==='guest'?'游客模式（未填令牌）':'');
    var noExtra=mode==='token'?'令牌只用于识别身份（不会获得额外权限），可看可删仍取决于该账号的角色与目录权限。':'';
    if(d.source==='openlist'&&d.openlist_ok){
      // d.openlist_user 是自检时 OpenList /api/me 回报的**身份名**，
      // 不是配置项（配置里只有令牌，见 issue #47）—— 变量名容易误读，故加注。
      var who=d.openlist_user||'游客';
      msg='OpenList 可用（'+(modeTxt||'已鉴权')+'，身份：'+who+'），已实测列出「'
        +(d.openlist_browse_path||'/')+'」共 '+(d.openlist_count||0)+' 项，媒体库目录将由其回答。'+noExtra;
      showToast('OpenList 连接正常',true);
    }else if(d.openlist){
      var kind=d.openlist_fail_kind||'';
      // 标题按「用户该做什么」分岔，而不是一律「不可用」（issue #47）：
      // 超时的英文/中文原样透出时，用户会把它当作「OpenList 坏了」去改配置，
      // 而实际上唯一该做的是「等一会重试」。
      var title=kind==='timeout'?'OpenList 响应超时':'OpenList 不可用';
      msg=title+'：'+(d.openlist_message||'未知原因')+'。媒体库已回落到 QAS 直连。';
      // 提示必须与「失败归类」一一对应，否则会把用户引向错误的修改动作。
      if(d.openlist_stage==='list'&&kind!=='timeout'){
        // 登录已通过 → 不是凭据问题，别再让用户去翻令牌/密码。
        msg+='（登录已通过，问题出在列目录 —— 多半是「路径前缀」没填对）';
      }
      if(kind==='timeout'){
        msg+='（OpenList 有响应但太慢：这通常**不是配置错误**，而是它正在等网盘返回目录。'
           +'请稍后重试；本次自检已按 '+(d.openlist_timeout_seconds||30)+' 秒预算等待。）';
      }
      if(d.openlist_auth&&!d.openlist_auth.token_set){msg+='当前没有可用凭据（游客模式，令牌留空），若 OpenList 关闭了游客访问，请在下方填访问令牌。'}
      showToast(title,false);
    }else{
      msg='未配置 OpenList 地址，媒体库目录由 QAS 直连回答（较慢）。';
      showToast('未配置 OpenList',false);
    }
    // 顺序要紧：_applyOpenListSubState 会按「已配置」把面板收起，
    // 失败结论若先渲染就会被它盖掉（面板收起时用户看不到任何原因）。
    // 先定基准态，再由 _renderOpenListHint 在失败时强制展开。
    _applyOpenListSubState(!!d.openlist);
    _renderOpenListHint(hint, msg, d);
  }catch(e){
    if(hint)hint.textContent='自检失败：'+(e&&e.message?e.message:'网络错误');
    showToast('自检失败',false);
  }finally{
    if(btn)btn.classList.remove('loading');
  }
}
