// ============ 媒体库（双向管理，issue #27） ============
// 目标：把「夸克网盘真实文件」与「QAS 转存任务」显式展示出来，并允许在网盘侧删除后
// 联动清理 DBAuto 的转存历史——否则网盘删了、本地仍判为「已转存」，重新转存会被跳过。
//
// 后端接口：
//   GET  /api/qas/tasks           QAS tasklist
//   GET  /api/qas/library?path=   网盘目录（数据源见下）
//   GET  /api/library/source      数据源自检（OpenList 是否可用）
//   GET  /api/openlist/tasks/audit QAS 任务 ↔ 网盘内容 对账（只读）
//   POST /api/qas/delete          {entries:[...], sync_history, source} 删文件
//   POST /api/qas/delete_tasks    {tasks:[{taskname,savepath}]} 删除 QAS 任务配置
//                                 （QAS 为自部署服务，改动只影响自己的任务表）
//
// ---- 目录数据源（issue #27 追问） ----
// 用户问「可以通过调用 openlist 进行管理吗…当前通过夸克自动转存的媒体库访问速度太慢了」。
// 「慢」的链路是：浏览器 → DBAuto → QAS → 夸克开放接口，每层都实时往返且 QAS
// 没有目录缓存。现在目录可改由 OpenList 回答（它对夸克驱动有列表缓存）。
// 前端只做三件事：把 source / fallback 显示出来（用户要能归因快慢）、
// 删除时按**条目来源**回传 source（跨源拼路径会误删，后端也拦）、
// 以及提供「任务对账」这个只读巡检（拿文件侧真相核对任务侧）。
//
// ---- 为什么不显示图片（issue #27 反馈「有一些有图，有一些没图，太丑了」） ----
// 上一版卡片会拿文件名去 TMDB 匹配海报。问题是网盘目录名/文件名与 TMDB 的命中率
// 天然不可能全中：命中就贴着海报、未命中就是一大块灰色占位块，同一行里两种形态混排，
// 越大的目录越花。用户明确要求「干脆直接不要图了」，因此本轮：
//   1. 卡片不再有缩略图/海报/评分角标，改为「图标 + 名称 + 元信息」的等高紧凑卡；
//   2. 前端不再调用 /api/tmdb/match（后端接口与匹配实现保留，未删除）；
//   3. 「图文视图 / 列表视图」改名为「卡片视图 / 列表视图」—— 已经没有图了。
//
// ---- 加载慢的根因与优化（issue #27 反馈「网盘文件模块访问有点慢」） ----
// 1. 根因一：单次目录请求的失败预算被拉到 33s。
//    后端 /api/qas/library 走 QASClient(timeout=15)，底层 http_get 对网络层错误
//    会重试 3 次。QAS 不可达/超时时用户实际等待最坏 ≈ 15+1+5+5+5+1 = 32s，
//    期间页面只有「加载中...」，观感就是「卡死」。
//    → 本页目录请求改为**短预算 + 单次轻量重试 + 8s 软超时**（见 libFetch），
//      失败立刻给出可重试的错误态，而不是让用户盯着转圈。
// 2. 根因二：首屏并行发 2 个请求，且「刷新」没有任何本地复用。
//    → 目录请求与任务请求**并行**发起，互不阻塞（先渲染先到的）；
//      同一目录按 TTL 复用最近一次响应（翻进子目录再返回不再打网络）。
// 3. 根因三：缺少进度反馈，无法区分「慢」与「死了」。
//    → 显式 loading 状态 + 时间轴提示（「已用 3.2s，正在等 QAS 响应…」）。
// 4. 渲染保持增量不变：加载中/错误/空态用静态 HTML 一次性赋值，不走多次重排。

// 5. 选择操作要能用键盘完成（issue #53：「选中效率太低了，没有批量全选和小部分
//    范围选择的快捷键」）。列表里的勾选框原本只能靠鼠标一个一个点，
//    键盘（Tab/空格）只能在**渲染顺序**里逐个走 —— 既选不中「前 5 个」，
//    也选不全 70 多个条目，更看不见自己当前在哪一行。
//    → Ctrl/⌘+A 全选、Shift+点击 / Shift+↑↓ 选范围、↑↓ 移焦点、
//      Ctrl/⌘+↕ 移动焦点但不改变选择、Space 切换、Esc 清空。见 _libKeydown。

// 媒体库根目录（issue #27）：把「资源根目录」设置项当作浏览下界。
// 默认 '/'（网盘根）；用户可在设置里指定 /影视、/媒体库 等，媒体库页即以此为起点。
function libRoot(){
  var r = (typeof APP_PATHS !== 'undefined' && APP_PATHS && APP_PATHS.library_root) || '/';
  r = String(r).trim();
  if(!r || r.charAt(0) !== '/') return '/';
  return r.replace(/\/+$/,'') || '/';
}
function libAtRoot(path){
  var p = String(path == null ? '' : path).replace(/\/+$/,'') || '/';
  return p === libRoot();
}
var libPath = libRoot();
var libEntries = [];
var libSelected = {};
// 键盘选择的两个状态（issue #53）：
//   libFocusKey    —— 当前行（虚线框），Shift+Range 的焦点端；
//   libSelectAnchor —— 范围选择的锚点（不动的那一端）。
// 两者都存「key」而不是下标：列表刷新/换视口后下标会变，key 不会。
var libFocusKey = null;
var libSelectAnchor = null;
// 上一次「范围选择」的基线（起点/终点 + 那次范围之前的选择）。没有它，
// Shift 只能扩不能缩（见 _libSelectRange 的推导）。
var libRangeBase = null;
var qasTasks = [];
var qasSelected = {};      // QAS 任务的勾选状态：key -> task

// ---------- 任务筛选状态（issue #53 追问：「转存任务的管理可以加入路径筛选」） ----------
// 为什么需要：任务数常见 200+（实测 291 条），一屏只看得到十几行。想找「电视剧里
// 有哪些没配好」只能靠人眼翻 6 屏 —— 这是纯粹的体力活。
//
// 三段式，从粗到细：
//   path   —— 转存路径包含（核心诉求，输入框 + 前缀 chips 两个入口写同一个变量）
//   name   —— 任务名包含（找到那一条）
//   分页   —— 有界首屏，避免 291 行全渲染
//
// 两个刻意的设计：
//  1. **筛选只在内存里做**（/api/qas/tasks 本来就是一次全量拉回），
//     不发新请求：输入即筛选，不会因为打字而打爆 QAS。
//  2. **筛选后勾选的一律是「看得见的行」**。删除按钮的文案会如实写出
//     「全部 N 条 / 筛选结果 N 条」，见 _qasVisibleKeys / doDeleteQasTasks。
var qasFilter = { path: '', name: '' };
// 分页状态。筛选条件一变就回到第 1 页 —— 否则「第 7 页 + 新筛选」会得到空列表，
// 用户只会认为「筛选坏了」。
var qasPageNo = 1;
var qasPageSize = (function(){
  try{
    var n = parseInt(localStorage.getItem('qas_page_size'), 10);
    if([10,20,50,100].indexOf(n) >= 0) return n;
  }catch(e){}
  return 20;
})();
var QAS_CHIP_LIMIT = 12;   // 路径前缀 chips 最多显示几个（其余归入输入框）
var QAS_CHIP_MIN = 3;      // 前缀至少出现几次才配得上一个 chip（避免全是「只有 1 条」）

// 目录响应缓存：短时间内重复进入同一目录直接复用，避免无谓往返。
// 只缓存「成功」结果；失败不缓存，用户重试才会真的重试。
var LIB_CACHE_TTL = 60000;
var libCache = {};        // path -> {ts, entries}
var libInflight = {};     // path -> Promise（同一目录并发只打一次）

// 展示形态（issue #27 + issue #53）。
//
// 这里从前是一个布尔：`lib_view = 'card' | 'list'`，按钮在两者之间二选一。
// 但用户要的从来是两件事，被塞进了一个按钮：
//   ① 「卡片还是表格」（**布局**）—— issue #27 引入的；
//   ② 「一屏能看多少条」（**密度**）—— issue #53 追问：「新增切换显示密度的功能
//      当前只能列表显示，实在是信息密度太低了」。
// 二选一的按钮把这层理解偏差固化了下来：想「看得更多」就只有列表视图一条路，
// 而列表视图一屏也就 20 行上下。本轮把它拆成**两维**：
//   * 布局：card（卡片）/ list（表格）
//   * 密度：comfortable（舒适）/ compact（紧凑）
//
// 关键实现决策（都是踩过坑才这么定的）：
//   1. 旧值 `'card' | 'list'` 必须被**读懂**，不能让用户「打开就回到默认」；
//      见 _migrateLibPrefs()；
//   2. 密度存在**单独的键**里，不做「4 个枚举值」的合并键：那样以后想加第三种布局
//      就得再迁一次存储，两维各自独立演进更稳；
//   3. 布局按钮与密度按钮**共用同一条渲染路径**（都调 _applyLibView()），
//      不存在「密度切了、键盘选择失灵」这类分叉；
//   4. 键盘选择逻辑查的是 `#libGrid` 或 `.lib-table`，与密度无关 ——
//      紧凑视图仍是 .lib-card（带 data-key），所以 Ctrl+A / Shift+连选照旧生效。
var LIB_VIEW_KEY = 'lib_view';            // 'card' | 'list'
var LIB_DENSITY_KEY = 'lib_density';      // 'comfortable' | 'compact'
var LIB_DENSITIES = ['comfortable', 'compact'];
var libView = 'card';
var libDensity = 'comfortable';

function libViewMode(){
  try{
    var v = localStorage.getItem(LIB_VIEW_KEY);
    if(v === 'list' || v === 'card') return v;
  }catch(e){}
  return 'card';
}

function libDensityMode(){
  try{
    var v = localStorage.getItem(LIB_DENSITY_KEY);
    if(LIB_DENSITIES.indexOf(v) >= 0) return v;
  }catch(e){}
  return 'comfortable';
}

// 旧偏好迁移：'card' | 'list' 已经能表达布局，**不需要**迁移（读写口径不变）。
// 独立出来是为了给「以后再加一档密度」留一个显式的入口，
// 而不是让读存储的逻辑散落在三处。
function _migrateLibPrefs(){
  var v = libViewMode(), d = libDensityMode();
  libView = (v === 'list') ? 'list' : 'card';
  libDensity = (d === 'compact') ? 'compact' : 'comfortable';
}

// 布局与密度的**唯一**落点：写存储 → 更新控件文案 → 原地重渲染当前目录。
// 传 opts.save=false 时不写存储（用在「从存储初始化」这条路径上）。
function _applyLibView(opts){
  opts = opts || {};
  if(opts.save !== false){
    try{
      localStorage.setItem(LIB_VIEW_KEY, libView);
      localStorage.setItem(LIB_DENSITY_KEY, libDensity);
    }catch(e){}
  }
  _updateLibViewBtn();
  _updateLibDensityBtn();
  // 只重画目录区（不重新发请求）：切视图不能顺带打网络，否则「密度切换」
  // 在慢接口下会变成一次几秒的等待 —— 用户按的是展示开关，不是刷新。
  _renderLibrary(libPath, libEntries, false, null, true);
}

function toggleLibView(){
  libView = (libView === 'card') ? 'list' : 'card';
  _applyLibView();
}

function toggleLibDensity(){
  libDensity = (libDensity === 'comfortable') ? 'compact' : 'comfortable';
  _applyLibView();
}

function _updateLibViewBtn(){
  var btn = document.getElementById('libViewBtn');
  if(!btn) return;
  btn.textContent = (libView === 'card') ? '列表视图' : '卡片视图';
  btn.setAttribute('aria-label', (libView === 'card') ? '切换为列表视图' : '切换为卡片视图');
  btn.setAttribute('aria-pressed', libView === 'list' ? 'true' : 'false');
}

function _updateLibDensityBtn(){
  var btn = document.getElementById('libDensityBtn');
  if(!btn) return;
  var compact = (libDensity === 'compact');
  btn.textContent = compact ? '舒适密度' : '紧凑密度';
  btn.setAttribute('aria-label', compact ? '切换为舒适密度' : '切换为紧凑密度');
  // aria-pressed 暴露的是「当前是不是紧凑密度」，屏幕阅读器据此读出状态
  btn.setAttribute('aria-pressed', compact ? 'true' : 'false');
  btn.title = compact ? '当前：紧凑（一屏显示更多条目）' : '当前：舒适（信息更宽松）';
}

// 单请求预算：与 QAS「本地服务」定位匹配，宁可快速失败也不让用户干等 30s+
//
// 注意：这里是**整次调用**的墙钟预算，而不是「每次尝试各 8s」。
// 踩坑记录（issue #27）：最初写成「每次尝试各自 setTimeout(8s) + 重试 1 次」，
// 实测最坏等待变成 8s + 退避 + 8s ≈ 16.4s —— 用户仍然在干等，
// 「快速失败」形同虚设。现在超时定时器只在进入循环时起一次，
// 到点即中止：无论重试到第几次，总等待都不会超过 LIB_REQ_TIMEOUT。
var LIB_REQ_TIMEOUT = 8000;   // 整次调用的软超时（AbortController）
var LIB_RETRIES = 1;          // 仅 1 次重试，失败立刻报错
var LIB_RETRY_DELAY = 400;    // 重试前的轻量退避

function fmtSize(n){
  if(!n || n <= 0) return '—';
  var u = ['B','KB','MB','GB','TB'], i = 0;
  while(n >= 1024 && i < u.length - 1){ n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + ' ' + u[i];
}

function fmtTs(ts){
  if(!ts) return '—';
  var d = new Date(ts * 1000);
  if(isNaN(d.getTime())) return '—';
  var p = function(x){ return (x < 10 ? '0' : '') + x; };
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

// 紧凑时间（月-日 时:分）：卡片很窄时用的降级格式，避免时间被省略号吃掉。
// 完整时间（含年份）始终在 title 与移动端布局里可见，见 _libMetaHtml。
function fmtTsShort(ts){
  var full = fmtTs(ts);
  if(full === '—') return full;
  var m = /^\d{4}-(\d{2}-\d{2} \d{2}:\d{2})$/.exec(full);
  return m ? m[1] : full;
}

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

// 把字符串安全地嵌进内联事件处理器的参数位（属性用双引号包裹）。
//
// 踩坑记录（issue #27）：直接用 JSON.stringify 会产出 "f2"（带双引号），
// 与 onchange="..." 的双引号冲突，HTML 解析后属性被截断：
//   onchange="toggleLibSelect(" f2",="" this.checked)"
// 于是勾选毫无反应。这里改为产出**单引号**字符串字面量，
// 并转义反斜杠、单引号与 < & " ，避免破坏属性与注入。
function jsArg(v){
  var t = String(v == null ? '' : v)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/</g, '\\u003c')
    .replace(/&/g, '\\u0026')
    .replace(/"/g, '\\u0022')
    // 控制字符（含 NUL）必须转义成 \uXXXX 再拼进内联属性：裸控制字符会被
    // HTML 解析器改写成 U+FFFD，参数回传时已与原文不符（issue #27 删除按钮失效）。
    .replace(/[\u0000-\u001f\u007f]/g, function(c){
      return '\\u' + ('000' + c.charCodeAt(0).toString(16)).slice(-4);
    });
  return "'" + t + "'";
}

// 当前目录回答者：'openlist' | 'qas'，由后端在目录响应里如实给出。
// 它同时决定删除走哪条链路 —— 谁列出来的条目，就删谁那边的（见 doDelete）。
var libSource = 'qas';
var libFallback = false;
var libFallbackReason = '';
var libFallbackHint = '';
// 回落归类（issue #47）：只给一句「不可用」，用户无从判断该改哪儿。
var libFallbackKind = '';
// 是否处于「OpenList 刚失败」的自动退避窗口（issue #47：「经常回落」）。
// 退避是短暂且自动的 —— 这类回落不该被描述成「配置坏了」。
var libFallbackBackoff = false;
// 退避窗口还剩多少秒（后端给的实测值：窗口长度 - 已过时间）。
// 0 = 不知道剩多久，此时文案退化为「稍后自动重试」，**不编造**倒计时。
var libFallbackBackoffLeft = 0;
// 本轮响应里被丢弃的「无名条目」数量（渲染不出来，但要如实告诉用户）。
var libHiddenNoName = 0;

// 数据源徽标：让「变快了 / 变慢了」能被归因。
// 不显示徽标的代价是：用户只能看到「快了点」，不知道是因为 OpenList 生效、
// 还是因为这次运气好（缓存命中）。回落时必须明确标出来 ——
// 否则 OpenList 挂掉后用户会以为自己在用 OpenList，实际走的是慢链路。
// 退避倒计时的文案片段。
// 后端把「窗口长度 + 记录时刻」一起给了（openlist.list_dir → backoff_seconds /
// backoff_since），这里只负责把它翻译成一句话 —— 两边分离的意义是：
// 窗口长度改了不用同步改前端文案，前端也不会凭空造出一个「约 20 秒」。
// 拿不到真实剩余时**宁可不说数字**：一个编造的倒计时会让用户在不该重试的
// 时刻重试，然后又一次看到回落，反而更像「坏了」。
function _backoffLeftText(){
  if(!libFallbackBackoffLeft) return '稍后（约 20 秒内）';
  return '约 ' + libFallbackBackoffLeft + ' 秒内';
}

// 回落排查弹窗：把「该去改哪个设置」直接讲清楚。
// 实测下来「配了 OpenList 却仍回落」几乎都是路径前缀问题（OpenList 的挂载点
// 不是网盘根），但表现和「OpenList 挂了」完全一样，所以这里按原因分流。
function _showFallbackHelp(){
  var reason = libFallbackReason || '未知原因';
  var tips;
  if(libFallbackKind === 'timeout'){
    // 超时是唯一「配置可能是对的」的失败：必须最先说清，否则用户会把
    // 地址/令牌/前缀全部重填一遍（issue #47 正是如此）。
    tips = 'OpenList 有响应但太慢（本次等待超时）—— 这**通常不是配置错误**，'
         + '多半是它正在等网盘（如夸克）返回列表。\n'
         + (libFallbackBackoff
            ? '系统正在自动重试（' + _backoffLeftText() + '自动回到 OpenList），**不需要改任何配置**。'
            : '建议：稍后重试；若每次都超时，说明该目录在网盘侧确实很慢，'
              + '可先用 OpenList 自己的网页界面确认目录能不能列出来。');
  }else if(libFallbackHint === 'path_prefix'){
    tips = 'OpenList 上找不到这个目录 —— 多半是「路径前缀」没填对。\n'
         + '打开「设置 → 数据源 → 媒体库文件源（OpenList）」，在「路径前缀」里填 OpenList'
         + '上挂载夸克网盘的那个目录（例如 /夸克），然后点「连接自检」。';
  }else{
    // 兜底文案指的是「我还没法归类」的失败，**不能**凭空点名某个字段：
    // 让用户去翻一个与实际原因无关的设置，比不给建议更糟（他会把本来对的
    // 配置改坏）。这里只说「去自检看归类」，并把鉴权口径写成现状
    // （令牌 / 游客），不再提已被移除的账号密码。
    tips = '请到「设置 → 数据源 → 媒体库文件源（OpenList）」点「连接自检」，'
         + '它会给出具体归类与原因：\n'
         + '· 连不上 → 核对地址与端口\n'
         + '· 鉴权失败 → 重新取一个访问令牌填进去（OpenList 只支持令牌鉴权）\n'
         + '· 找不到该目录 → 检查「路径前缀」';
  }
  // 用应用内 toast（原生 alert 会阻塞渲染，也无法在走查脚本里断言）
  showToast('已回落到 QAS 直连：' + reason + ' ' + tips.replace(/\n+/g, ' '), false, 12000);
}

function _renderLibSourceBadge(){
  var el = document.getElementById('libSourceBadge');
  if(!el) return;
  if(!libEntries.length && !libCountText()){
    el.style.display = 'none';
    // 隐藏时**必须把上一轮的结论一起清掉**。
    //
    // 踩坑（本轮走查实测）：只把元素藏起来、留着旧的 textContent 与 title，
    // 下一次（失败态）虽然不显示，但 title 仍带着「OpenList 超时…已回落到 QAS」
    // 这类结论 —— 只要有任何路径让它重新可见（或用户/脚本去读 title），
    // 页面就在说一件早已不成立的事。徽标是**断言**，隐藏不等于撤销断言。
    el.textContent = '';
    el.removeAttribute('title');
    el.onclick = null;
    el.style.cursor = '';
    return;
  }
  var label, title;
  if(libSource === 'openlist'){
    label = 'OpenList';
    title = '目录由 OpenList 回答（对夸克驱动有列表缓存，通常更快）';
  }else{
    label = libFallback ? 'QAS（OpenList 回落）' : 'QAS 直连';
    if(libFallback){
      // 回落原因必须能看见、能点开（issue #27 追问「配了 OpenList 却仍回落」）：
      // 「已回落」本身不是信息，用户需要知道是地址错了还是路径前缀不对。
      // 所以 title 直接给结论 + 修复方向，点开是更完整的步骤。
      var fix = libFallbackKind === 'timeout'
        ? (libFallbackBackoff
           ? 'OpenList 响应超时（不是配置错误），系统正在自动重试，' + _backoffLeftText() + '自动回到 OpenList。'
           : 'OpenList 响应超时（多半是网盘侧目录拉取慢，不是配置错误）：稍后重试即可。')
        : (libFallbackHint === 'path_prefix'
          ? '多半是「路径前缀」没填对：设置 → 数据源 → OpenList 里填 OpenList 挂载夸克网盘的目录。'
          : '到「设置 → 数据源 → OpenList」点「连接自检」看具体原因。');
      title = (libFallbackKind === 'timeout' ? 'OpenList 响应超时，已回落到 QAS 直连（较慢）。原因：' : 'OpenList 不可用，已回落到 QAS 直连（较慢）。原因：')
        + (libFallbackReason || '未知') + fix + '（点此查看完整排查步骤）';
      el.onclick = function(){ _showFallbackHelp(); };
      el.style.cursor = 'pointer';
    }else{
      title = '目录由 QAS 直连回答（未配置 OpenList）。配置 OpenList 可显著加快目录浏览';
      el.onclick = null;
      el.style.cursor = '';
    }
  }
  if(libSource === 'openlist'){ el.onclick = null; el.style.cursor = ''; }
  el.textContent = label;
  el.title = title;
  el.className = 'lib-src-badge lib-src-' + (libSource === 'openlist' ? 'ol' : 'qas');
  el.style.display = '';
}

function libCountText(){
  var el = document.getElementById('libCount');
  return el ? el.textContent : '';
}

function loadLibraryPage(){
  // 目录与任务互不依赖：并行发起，先到先渲染（原先串行 await 白等一倍时间）
  _migrateLibPrefs();
  _updateLibViewBtn();
  _updateLibDensityBtn();
  // 首次进入且尚未导航过时，落到设置里的「媒体库根目录」。
  // （libPath 在脚本加载期就已初始化为当时的 libRoot，此处覆盖为最新配置）
  loadLibrary(libPath);
  loadQasTasks();
  _syncLibRootFromSettings();
}

// 配置是异步拉取的：首次进入库页时把起点对齐到最新配置的根目录。
// 仅当「当前仍在初始起点（尚未导航）」时重定位，避免打断用户已翻进去的目录。
var _libRootSynced = false;
async function _syncLibRootFromSettings(){
  if(_libRootSynced) return;
  _libRootSynced = true;
  var initial = libPath;
  try{
    // 复用 library.js 的短预算通道（本文件禁用 apiGet，避免叠加长重试等待）
    var r = await libFetch('/api/settings/all');
    var all = (r && r.ok && r.data) ? r.data : null;
    if(all) SETTINGS_ALL = all;
    var sp = (all && all.schedule && all.schedule.savepaths) || {};
    if(!sp.library_root) return;
    APP_PATHS.library_root = sp.library_root;
    var root = libRoot();
    if(root !== initial && libPath === initial){
      loadLibrary(root, {force:true});   // 用户没动过，直接对齐到配置根目录
    }
  }catch(e){}
}

// ---------- 短预算请求（见文件头「加载慢的根因」） ----------
// 返回 {ok:true, data, ms} / {ok:false, message, ms}，不抛异常。
// 超时预算覆盖整个重试过程（见 LIB_REQ_TIMEOUT 的踩坑说明）。
async function libFetch(url){
  var t0 = Date.now();
  var lastErr = null;
  var ctrl = null;
  if(typeof AbortController === 'function'){
    ctrl = new AbortController();
    setTimeout(function(){ try{ ctrl.abort(); }catch(e){} }, LIB_REQ_TIMEOUT);
  }
  for(var i = 0; i <= LIB_RETRIES; i++){
    if(ctrl && ctrl.signal && ctrl.signal.aborted){
      lastErr = new Error('请求超时（' + (LIB_REQ_TIMEOUT/1000) + 's）');
      break;
    }
    try{
      var r = await fetch(url, { headers: authHeaders(), signal: ctrl ? ctrl.signal : undefined });
      if(r.status === 401){
        TOKEN = ''; localStorage.removeItem('auth_token');
        showToast('登录已过期，正在跳转...', false);
        setTimeout(function(){ location.href = '/login.html'; }, 800);
        return { ok:false, message:'登录已过期', ms: Date.now() - t0 };
      }
      if(!r.ok){
        lastErr = new Error('HTTP ' + r.status);
      }else{
        return { ok:true, data: await r.json(), ms: Date.now() - t0 };
      }
    }catch(e){
      // 软超时 / 网络异常：都走「快速失败」路径，不再叠加后端级长重试
      lastErr = (e && e.name === 'AbortError')
        ? new Error('请求超时（' + (LIB_REQ_TIMEOUT/1000) + 's）')
        : e;
    }
    if(i < LIB_RETRIES && !(ctrl && ctrl.signal && ctrl.signal.aborted)){
      await new Promise(function(res){ setTimeout(res, LIB_RETRY_DELAY); });
    }
  }
  return { ok:false, message: (lastErr && lastErr.message) || '请求失败', ms: Date.now() - t0 };
}

// ---------- QAS 任务 ----------

async function loadQasTasks(){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  qasSelected = {};
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">加载中...</div></div>';
  var r = await libFetch('/api/qas/tasks');
  if(!r.ok){
    _renderQasTasksError(r.message);
    return;
  }
  var res = r.data || {};
  if(!res.success){
    _renderQasTasksError(res.message || '请检查 QAS 配置');
    return;
  }
  qasTasks = res.tasks || [];
  _renderQasTasks();
}

// ---------- 任务筛选（issue #53 追问） ----------

// 大小写不敏感的子串匹配。QAS 的路径/任务名可能含全角与大小写混排，
// 这里统一走 toLowerCase()；中文不受影响，英文路径（/TV、/Anime）能对齐。
function _qasMatch(t){
  var p = qasFilter.path.trim().toLowerCase();
  var n = qasFilter.name.trim().toLowerCase();
  if(p){
    var sp = String(t.savepath == null ? '' : t.savepath).toLowerCase();
    if(sp.indexOf(p) < 0) return false;
  }
  if(n){
    var tn = String(t.taskname == null ? '' : t.taskname).toLowerCase();
    if(tn.indexOf(n) < 0) return false;
  }
  return true;
}

function filteredQasTasks(){
  return qasTasks.filter(_qasMatch);
}

function qasFilterActive(){
  return !!(qasFilter.path.trim() || qasFilter.name.trim());
}

// 当前页可见的任务。**这是删除安全的唯一依据**：任何批量操作都必须经过这里，
// 否则「筛选出 5 条却删掉 291 条」。
function _qasVisibleTasks(){
  var out = filteredQasTasks();
  if(qasPageSize > 0){
    var start = (qasPageNo - 1) * qasPageSize;
    out = out.slice(start, start + qasPageSize);
  }
  return out;
}

function _qasVisibleKeys(){
  return _qasVisibleTasks().map(qasKey);
}

// 筛选条件变化：回到第 1 页（理由见 qasPageNo 的注释），再重渲染。
// 勾选**不清空**，但会把「已选但已被筛掉」的行在操作条里如实标注出来 ——
// 静默带着隐藏的勾选去删除，是这类界面最危险的行为。
// 输入框 → 模型的**唯一**入口。
// 踩坑记录：最初这里只写「重置页码 + 重渲染」，以为输入框的 value 会被渲染逻辑
// 自动收编 —— 结果打字时 input 事件确实在跑、界面却纹丝不动（模型永远是空的），
// 走查脚本第 1 条断言就把它抓出来了（「输入 /影视/电视剧 后仍是 9 行」）。
// 从这里读写 value，再交给 _renderQasFilterBar 做回写，保证模型是唯一真相。
function onQasFilterInput(){
  var p = document.getElementById('qasFilterPath');
  var n = document.getElementById('qasFilterName');
  qasFilter.path = p ? p.value : '';
  qasFilter.name = n ? n.value : '';
  qasPageNo = 1;
  _renderQasTasks();
}

function onQasFilterKeydown(e){
  if(!e) return;
  if(e.key === 'Escape'){
    // 只清筛选，不影响媒体库的选择（Esc 在媒体库里的语义见 _libKeydown）
    if(qasFilterActive()){
      clearQasFilter();
      e.stopPropagation();
      e.preventDefault();
    }
  }else if(e.key === 'Enter'){
    qasPageNo = 1;
    _renderQasTasks();
    e.preventDefault();
  }
}

function setQasPathFilter(v){
  qasFilter.path = v == null ? '' : String(v);
  qasPageNo = 1;
  var el = document.getElementById('qasFilterPath');
  if(el) el.value = qasFilter.path;
  _renderQasTasks();
}

function clearQasFilter(){
  qasFilter.path = '';
  qasFilter.name = '';
  qasPageNo = 1;
  var p = document.getElementById('qasFilterPath');
  if(p) p.value = '';
  var n = document.getElementById('qasFilterName');
  if(n) n.value = '';
  _renderQasTasks();
}

function setQasPageSize(v){
  var n = parseInt(v, 10);
  qasPageSize = [10,20,50,100].indexOf(n) >= 0 ? n : 20;
  try{ localStorage.setItem('qas_page_size', String(qasPageSize)); }catch(e){}
  qasPageNo = 1;
  _renderQasTasks();
}

function qasPage(delta){
  var pages = _qasPageCount();
  var next = qasPageNo + delta;
  if(next < 1 || next > pages) return;
  qasPageNo = next;
  _renderQasTasks();
}

function _qasPageCount(){
  if(!qasPageSize) return 1;
  var total = filteredQasTasks().length;
  return Math.max(1, Math.ceil(total / qasPageSize));
}

// 路径前缀 chips：把 291 条任务的 savepath 归并成「前 2 层」的分类，
// 命中最多的排前面并标注条数 —— 用户点之前就知道会筛出多少条。
//
// 为什么取 2 层：实测路径形如 /影视/电视剧/瑞克和莫蒂 第九季，
// 第 1 层（/影视）太粗（等于不筛），第 3 层已经接近单条任务（等于搜索）。
function _qasPathPrefixes(){
  var counts = {}, order = [];
  qasTasks.forEach(function(t){
    var segs = String(t.savepath == null ? '' : t.savepath).split('/').filter(Boolean);
    if(!segs.length) return;
    var key = '/' + segs.slice(0, 2).join('/');
    if(counts[key] == null){ counts[key] = 0; order.push(key); }
    counts[key]++;
  });
  return order
    .filter(function(k){ return counts[k] >= QAS_CHIP_MIN; })
    .sort(function(a, b){ return counts[b] - counts[a] || (a < b ? -1 : 1); })
    .slice(0, QAS_CHIP_LIMIT)
    .map(function(k){ return { path: k, n: counts[k] }; });
}

function _renderQasFilterBar(){
  var bar = document.getElementById('qasFilterBar');
  if(!bar) return;
  // 没有任务时筛什么筛：整条筛选栏藏起来，不给空界面加控件
  bar.style.display = qasTasks.length ? 'flex' : 'none';

  // 输入框与状态同步（重渲染 / chips 点击 / 程序化设置都走这里，单一出口）
  var pEl = document.getElementById('qasFilterPath');
  if(pEl && pEl.value !== qasFilter.path) pEl.value = qasFilter.path;
  var nEl = document.getElementById('qasFilterName');
  if(nEl && nEl.value !== qasFilter.name) nEl.value = qasFilter.name;

  var clearBtn = document.getElementById('qasFilterClear');
  if(clearBtn) clearBtn.disabled = !qasFilterActive();

  // 路径前缀 chips
  var chips = document.getElementById('qasPathChips');
  if(chips){
    var pref = _qasPathPrefixes();
    var html = pref.map(function(pf){
      var on = qasFilter.path === pf.path;
      return '<button type="button" class="task-chip' + (on ? ' on' : '') + '"' +
        ' title="' + esc('转存路径以 ' + pf.path + ' 开头 · ' + pf.n + ' 条') + '"' +
        ' onclick="setQasPathFilter(' + jsArg(on ? '' : pf.path) + ')">' +
        esc(pf.path) + '<i>' + pf.n + '</i></button>';
    }).join('');
    chips.innerHTML = html;
    chips.style.display = pref.length ? 'flex' : 'none';
  }

  // 分页控件
  var pages = _qasPageCount();
  var pos = document.getElementById('qasPagerPos');
  if(pos) pos.textContent = '第 ' + qasPageNo + ' / ' + pages + ' 页';
  var prev = document.getElementById('qasPrev');
  if(prev) prev.disabled = qasPageNo <= 1;
  var next = document.getElementById('qasNext');
  if(next) next.disabled = qasPageNo >= pages;
  var sizeEl = document.getElementById('qasPageSize');
  if(sizeEl && String(sizeEl.value) !== String(qasPageSize)) sizeEl.value = String(qasPageSize);
}

// QAS 任务的定位键：QAS 不保证 taskname 唯一，故用 taskname + savepath。
// 刷新后若任务被改名/移动，键会自然失配（勾选清空），不会误删别的任务。
//
// 分隔符原来用的是 NUL（\u0000），这会导致**删除按钮彻底失效**（issue #27）：
// 该 key 会经 jsArg 拼进内联 onclick 属性，而 HTML 解析器把属性里的裸 NUL
// 替换成 U+FFFD，于是回传的 key ≠ qasKey() 生成的 key，findQasTask 落空、
// 删除静默 return。改用可打印的「单位分隔符符号」\u241f：HTML 里是普通字符，
// 不会被改写，也几乎不可能出现在任务名/路径里。
var QAS_KEY_SEP = '\u241f';

function qasKey(t){
  return String(t.taskname == null ? '' : t.taskname) + QAS_KEY_SEP + String(t.savepath == null ? '' : t.savepath);
}

function _renderQasTasksError(message){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  var cnt = document.getElementById('qasTaskCount');
  if(cnt) cnt.textContent = '';
  var bar = document.getElementById('qasFilterBar');
  if(bar) bar.style.display = 'none';
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px">' +
    '<div class="empty-title">读取失败</div>' +
    '<div class="empty-desc">' + esc(message || '请检查 QAS 配置') + '</div>' +
    '<div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadQasTasks()">重试</button></div>' +
    '</div>';
  updateQasActionBar();
}

// 计数文案：筛选生效时必须同时给出「筛出多少 / 总共多少」。
// 只显示「共 8 条」会让用户以为任务被删了；只显示总数则看不出筛选效果。
function _qasCountText(){
  var total = qasTasks.length;
  if(!total) return '';
  var n = filteredQasTasks().length;
  if(!qasFilterActive()) return '共 ' + total + ' 条';
  return '筛选出 ' + n + ' / ' + total + ' 条';
}

function _renderQasTasks(){
  var box = document.getElementById('qasTaskList');
  if(!box) return;
  _renderQasFilterBar();

  var cnt = document.getElementById('qasTaskCount');
  if(cnt) cnt.textContent = _qasCountText();

  if(!qasTasks.length){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">暂无任务</div></div>';
    updateQasActionBar();
    return;
  }

  // 页码越界兜底：筛选/删除后总页数会变小，qasPageNo 可能指到空页
  var pages = _qasPageCount();
  if(qasPageNo > pages) qasPageNo = pages;

  var rows = _qasVisibleTasks();
  if(!rows.length){
    box.innerHTML = '<div class="task-filter-empty">没有匹配的任务。' +
      '<button class="btn btn-sm btn-outline" style="margin-left:8px" onclick="clearQasFilter()">清除筛选</button></div>';
    updateQasActionBar();
    return;
  }

  var head = '<div class="lib-row lib-head"><span class="lib-cb"></span><span>任务名</span><span>转存路径</span><span>分享链接</span><span></span></div>';
  if(qasPageSize && filteredQasTasks().length > qasPageSize){
    var start = (qasPageNo - 1) * qasPageSize + 1;
    var end = (qasPageNo - 1) * qasPageSize + rows.length;
    head = '<div class="lib-row lib-head"><span class="lib-cb"></span><span>任务名</span>' +
      '<span>转存路径</span><span>分享链接</span>' +
      '<span class="lib-ops" style="color:var(--text3);font-weight:400">' + start + '–' + end + '</span></div>';
  }
  var html = '<div class="lib-table lib-table-tasks">' + head;
  rows.forEach(function(t){
    var key = qasKey(t);
    // 行内只带 key（不把任务名拼进 onclick 字符串），避免引号/特殊字符破坏属性
    html += '<div class="lib-row">' +
      '<span class="lib-cb"><input type="checkbox" aria-label="选择 ' + esc(t.taskname) + '"' +
        (qasSelected[key] ? ' checked' : '') +
        ' onchange="toggleQasSelect(' + jsArg(key) + ', this.checked)"></span>' +
      '<span class="lib-name" title="' + esc(t.taskname) + '">' + esc(t.taskname || '—') + '</span>' +
      '<span class="lib-sub" title="' + esc(t.savepath) + '">' + esc(t.savepath || '—') + '</span>' +
      '<span class="lib-sub">' + (t.shareurl ? '<a href="' + esc(t.shareurl) + '" target="_blank" rel="noopener noreferrer">链接</a>' : '—') + '</span>' +
      '<span class="lib-ops"><button class="btn btn-sm btn-outline" onclick="deleteQasTask(' + jsArg(key) + ')">删除</button></span>' +
      '</div>';
  });
  html += '</div>';
  box.innerHTML = html;
  updateQasActionBar();
}

function findQasTask(key){
  for(var i = 0; i < qasTasks.length; i++){
    if(qasKey(qasTasks[i]) === key) return qasTasks[i];
  }
  return null;
}

function toggleQasSelect(key, on){
  var t = findQasTask(key);
  if(!t) return;
  if(on) qasSelected[key] = t; else delete qasSelected[key];
  updateQasActionBar();
}

function updateQasActionBar(){
  var bar = document.getElementById('qasActionBar');
  var n = Object.keys(qasSelected).length;
  if(bar) bar.style.display = qasTasks.length ? 'flex' : 'none';

  var cnt = document.getElementById('qasSelCount');
  if(cnt){
    var txt = n ? '已选 ' + n + ' 条' : '';
    // 勾选里有「当前筛选/当前页看不到的」行时如实标出。
    // 静默带着隐藏勾选去删除是最危险的失败模式（删的比看到的多）。
    if(n){
      var visible = {};
      _qasVisibleKeys().forEach(function(k){ visible[k] = 1; });
      var hidden = Object.keys(qasSelected).filter(function(k){ return !visible[k]; }).length;
      if(hidden) txt += '（其中 ' + hidden + ' 条不在当前筛选/页内）';
    }
    cnt.textContent = txt;
  }

  var btn = document.getElementById('qasDelBtn');
  if(btn) btn.disabled = n === 0;

  // 表头全选框只对「当前可见行」负责：筛选后点全选，语义是「全选这 N 条可见的」。
  var vis = _qasVisibleKeys();
  var visSel = vis.filter(function(k){ return !!qasSelected[k]; }).length;
  var all = document.getElementById('qasSelAll');
  if(all){
    all.checked = vis.length > 0 && visSel === vis.length;
    all.indeterminate = visSel > 0 && visSel < vis.length;
    all.disabled = vis.length === 0;
    all.title = qasFilterActive() || qasPageSize
      ? '选择当前筛选/页内可见的 ' + vis.length + ' 条'
      : '选择本目录全部条目';
  }
}

// 全选/取消全选**只作用于当前可见行**（筛选结果 + 当前页）。
// 旧实现是 qasSelected = {} 后遍历 qasTasks 全量勾选 —— 加了筛选/分页之后，
// 那等于「看着 20 行、点一下全选，实际勾上 291 条」。这里改成只动可见行，
// 且**保留**范围外的已有勾选（用户可能先勾了几条再翻页）。
function toggleQasSelectAll(on){
  _qasVisibleTasks().forEach(function(t){
    var key = qasKey(t);
    if(on) qasSelected[key] = t; else delete qasSelected[key];
  });
  // 重渲染以同步表头的全选态与各行的勾选框
  if(!on && qasSelected && !Object.keys(qasSelected).length) qasSelected = {};
  _renderQasTasks();
}

// ---------- 删除 QAS 任务（issue #27 跟进） ----------

// 删除 QAS 的条目任务配置。用户确认 QAS 是自部署服务，因此这里的改动只影响
// 自己的任务表，不存在「改别人的任务配置」的风险。
//
// 两个刻意的设计：
//  1. 删除前必须确认，且文案里写清「任务配置会被移除、网盘里已转存的文件不受影响」，
//     避免用户以为连文件一起删了；
//  2. 后端是**整表写回**（QAS /update 为整表替换语义），失败时后端不做任何改动。
// extra（issue #57）：本次删除附带的联动动作。目前只有一条 ——
// 「同步删除网盘上的同名条目」。它必须在**确认框文案里复述**（用户确认的是
// 删什么，不是一个模糊的「确定」），也必须随请求发给后端（否则勾了等于没勾）。
async function doDeleteQasTasks(items, label, extra){
  if(!items || !items.length) return;
  extra = extra || {};
  // 同 doDelete：确认框返回 false 时绝不能继续（issue #27）
  var extraText = extra.pathFilter
    ? '\n\n同时会删除网盘上的同名条目（仅限任务名能被文件/文件夹名匹配到的，且路径包含「' +
      extra.pathFilter + '」）。'
    : '\n\n同时会删除网盘上的同名条目（仅限任务名能被文件/文件夹名匹配到的）。';
  var confirmed = await confirmThen('删除 QAS 任务',
    '将从夸克自动转存中移除 ' + label + '。\n' +
    '只删除任务配置（分享链接/保存路径/匹配规则），网盘里已转存的文件不受影响。' +
    (extra.syncTasks ? extraText : ''),
    '删除', '取消');
  if(!confirmed) return;

  var btn = document.getElementById('qasDelBtn');
  if(btn) btn.classList.add('loading');
  try{
    var payload = { tasks: items };
    // 默认关：关着的时候连字段都不发 —— 一个「有默认值语义的布尔字段」
    // 一旦被前端写错（或后端默认值被改），就会变成静默删任务。
    if(extra.syncTasks){
      payload.sync_tasks = true;
      if(extra.pathFilter) payload.sync_tasks_path_filter = extra.pathFilter;
    }
    var res = await apiPost('/api/qas/delete_tasks', payload);
    if(res.success){
      // 联动结果必须如实分开报（issue #57）：任务删成功而网盘侧没匹配上、
      // 或者网盘侧只删了一半，都不能被一句「已删除 N 条任务」盖过去。
      var extraMsg = _tasksResultMsg(res, extra && extra.syncTasks);
      showToast('已删除 ' + res.deleted + ' 条 QAS 任务' + extraMsg, true);
      // 后端返回写回后的完整列表，直接复用可省掉一次往返
      if(res.tasks){
        qasTasks = res.tasks;
        qasSelected = {};
        _renderQasTasks();
        return;
      }
    }else{
      showToast(res.message || '删除失败，QAS 未做改动', false);
      return;
    }
  }catch(e){
    showToast('删除失败: ' + e.message, false);
    return;
  }finally{
    if(btn) btn.classList.remove('loading');
  }
  loadQasTasks();
}

function deleteQasTask(key){
  var t = findQasTask(key);
  if(!t) return;
  doDeleteQasTasks([{ taskname: t.taskname, savepath: t.savepath }], '任务「' + t.taskname + '」',
                   _syncTasksExtra([{ taskname: t.taskname, savepath: t.savepath }]));
}

// ---------- 删除范围开关（issue #57） ----------
//
// 用户诉求：「删除 OpenList 指定文件夹下的资源库文件时，同步删除同名自动夸克
// 转存任务」+ 一个开关。两个方向都要能开（网盘侧删文件 / QAS 侧删任务），
// 因为用户实际是两种操作都会做。
//
// 三条约束（每一条都对应一种「看起来能用、其实很危险」的实现）：
//   1. **默认关**：删任务不可逆，且「同名」这件事本身可能撞车（不同作品同名、
//      taskname 带「[已完结]」后缀）。不勾就绝不动 QAS 任务表。
//   2. **范围收紧到当前筛选**：用户在界面里只看到筛出来的那批任务，
//      联动就该只作用在这批的范围内。不带范围 = 拿一条文件名去删整表里的同名任务。
//   3. **结果如实分开报**：任务删成功、网盘侧 0 命中（或部分失败）必须分别说明，
//      不能用一句「已删除 N 条任务」把它们盖过去。
function _syncTasksExtra(items){
  var chk = document.getElementById('syncQasTasksChk');
  if(!chk || !chk.checked) return { syncTasks: false };
  return { syncTasks: true, pathFilter: qasFilter.path.trim(), items: items };
}

// 网盘侧删除时的同名任务联动（方向二）。
function _syncTasksFromLibExtra(entries){
  var chk = document.getElementById('syncQasTasksFromLibChk');
  if(!chk || !chk.checked) return { syncTasks: false };
  // 网盘侧没有「路径筛选」这一说，但**当前浏览目录**就是天然的范围：
  // 只允许删「保存路径与当前目录相关」的同名任务，避免拿文件名去撞全表。
  return { syncTasks: true, pathFilter: libAtRoot(libPath) ? '' : libPath,
           names: (entries || []).map(function(e){ return e && e.name; }).filter(Boolean) };
}

// 联动结果的统一回显（两个方向共用）。
// 返回一段可直接拼在 toast 后面的文案；没开联动时返回空串。
function _tasksResultMsg(res, syncTasks){
  if(!syncTasks) return '';
  var r = res && res.tasks_result;
  if(!r) return '；网盘侧联动未执行（后端未回报结果）';
  if(r.success){
    return '；网盘侧同名条目已删 ' + (r.deleted || 0) + ' 项' +
      (r.failed ? '（失败 ' + r.failed + ' 项）' : '');
  }
  // 任务已删成功，但网盘侧一条都没动 —— 必须说清「为什么」，否则用户以为删干净了
  return '；网盘侧同名条目未删除: ' + (r.message || '未匹配到同名条目');
}

function deleteSelectedQasTasks(){
  var keys = Object.keys(qasSelected);
  if(!keys.length) return;
  var items = keys.map(function(k){
    var t = qasSelected[k];
    return { taskname: t.taskname, savepath: t.savepath };
  });
  // 文案里写清「在哪批里选的」：筛选生效时只说「所选 5 条」，用户会怀疑
  // 「我明明有 200 条任务，它是不是要全删」。把筛选条件复述出来成本最低。
  var scope = qasFilterActive()
    ? '（筛选：' + [qasFilter.path.trim() ? '路径含「' + qasFilter.path.trim() + '」' : '',
                    qasFilter.name.trim() ? '任务名含「' + qasFilter.name.trim() + '」' : '']
        .filter(Boolean).join('、') + '）'
    : '';
  doDeleteQasTasks(items, '所选的 ' + items.length + ' 条任务' + scope,
                   _syncTasksExtra(items));
}

// ---------- 网盘文件 ----------

// ---------- 首帧反馈（issue #27 反馈：「每次打开都要等几秒」） ----------
//
// 上一版首帧只有一个空态文字块（约 60px 高）。目录响应一到就换成
// 12 张卡片的网格（几百 px 高），**页面整体向下跳一大截**。
// 跳动的代价不只是「难看」：用户刚点下的「刷新 / 上一级 / 视图切换」按钮
// 会被推到别处，第二次点击落到空白处 —— 观感上就是「这个页面很慢/很难用」。
//
// 所以首帧提前把高度占住：用与卡片视图完全相同的网格几何渲染 N 张骨架卡，
// 数据到达时在同一高度内替换内容，页面不位移。
// 键名与 .lib-skeleton 样式里的 --lib-sk-h 一一对应（骨架高度由 CSS 给）。
var LIB_SKELETON_ROWS = 8;                 // 首帧占位卡行数
var LIB_SKELETON_KEY = 'lib_sk_rows';      // 用上次真实条数校准骨架数量（避免又跳一次）

function _libSkeletonRows(){
  try{
    var n = parseInt(localStorage.getItem(LIB_SKELETON_KEY), 10);
    if(n >= 3 && n <= 24) return n;
  }catch(e){}
  return LIB_SKELETON_ROWS;
}

function _libRememberRows(n){
  try{ localStorage.setItem(LIB_SKELETON_KEY, String(n)); }catch(e){}
}

// 加载态：进度文案（可读、带已用时长）+ 与卡片网格同几何的骨架卡
function _libLoadingHtml(t0){
  var s = ((Date.now() - t0) / 1000).toFixed(1);
  var html = '<div class="lib-loading">' +
    '<div class="lib-loading-hint" id="libLoadingHint">已用 ' + s + 's，正在等 QAS 响应…' +
    '（超过 ' + (LIB_REQ_TIMEOUT/1000) + 's 会自动失败可重试）</div>' +
    '<div class="lib-grid lib-grid-skeleton" aria-hidden="true">';
  var n = _libSkeletonRows();
  for(var i = 0; i < n; i++) html += _libSkeletonCardHtml();
  html += '</div></div>';
  return html;
}

function _libSkeletonCardHtml(){
  return '<div class="lib-card lib-card-skeleton">' +
    '<span class="lib-sk lib-sk-cb"></span>' +
    '<span class="lib-sk lib-sk-icon"></span>' +
    '<span class="lib-card-name-col">' +
      '<span class="lib-sk lib-sk-line"></span>' +
      '<span class="lib-sk lib-sk-line lib-sk-line-sub"></span>' +
      '<span class="lib-sk lib-sk-line lib-sk-line-sub"></span>' +
    '</span>' +
    '<span class="lib-sk lib-sk-btn"></span>' +
    '</div>';
}

async function loadLibrary(path, opts){
  opts = opts || {};
  var box = document.getElementById('libList');
  if(!box) return;
  var target = path || '/';
  var force = !!opts.force;
  var t0 = Date.now();

  // ---- 缓存策略（issue #47 追问「上一级 OpenList 自动回落」的真因） ----
  //
  // 这里有一个**必须守住的不变式**：`meta`（数据源 / 是否回落 / 回落原因）
  // 是**时间敏感**的，而 `entries`（目录内容）不是。
  //
  //   目录内容 60s 内复用完全合理（文件不会每秒变一次）；
  //   但「这份数据是 OpenList 给的还是 QAS 兜底给的」60 秒就太久了 ——
  //   30s 内 OpenList 可能已经被修好/被改坏。
  //
  // 踩坑（本轮实测）：原来两条缓存分支都把缓存里的 meta 当作**当前**状态
  // 直接渲染，于是「根目录第一次回落 → 进子目录走 OpenList → 返回根目录」
  // 时徽标一直停在「QAS（OpenList 回落）」、提示说「OpenList 不可用」，
  // 而它其实完全正常。用户看到的现象就是「一返回上一级就回落」。
  //
  // 现在的口径：
  //   - 渲染缓存内容时**不对数据源下结论**（传 null = 只画内容、不动状态），
  //     也就是先让用户看到内容，徽标保持上一次已知的真实结论；
  //   - 只有在**真的打了一次网络并拿到当前响应**之后，才更新徽标。
  // 也就是说：徽标永远来自最近一次真实响应，绝不来自缓存里的旧断言。
  var hit = libCache[target];
  var fresh = hit && !force && (Date.now() - hit.ts) < LIB_CACHE_TTL;
  if(fresh){
    _renderLibrary(target, hit.entries, true, null, null);
    return;
  }
  if(hit && !force){
    // 陈旧数据先顶上，避免白屏；同样不对数据源下结论（见上）。
    _renderLibrary(target, hit.entries, true, null, null);
  }else{
    box.innerHTML = _libLoadingHtml(t0);
  }
  var hintTimer = setInterval(function(){
    var el = document.getElementById('libLoadingHint');
    if(!el){ clearInterval(hintTimer); return; }
    el.textContent = '已用 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's，正在等 QAS 响应…（超过 ' + (LIB_REQ_TIMEOUT/1000) + 's 会自动失败可重试）';
  }, 250);

  try{
    // 2. 同一目录并发请求合并为一次（例如连点「刷新」）
    var key = target;
    if(!libInflight[key]){
      // refresh=1：绕过后端目录缓存（后端默认 30s 内复用上次结果，见 media_library.py）
      var url = '/api/qas/library?path=' + encodeURIComponent(target) + (force ? '&refresh=1' : '');
      libInflight[key] = libFetch(url)
        .then(function(r){ delete libInflight[key]; return r; });
    }
    var r = await libInflight[key];
    clearInterval(hintTimer);

    if(!r.ok){
      if(!hit) _renderLibError(target, r.message, r.ms);
      else showToast('刷新失败：' + r.message + '（显示的是缓存数据）', false);
      return;
    }
    var res = r.data || {};
    if(!res.success){
      if(!hit) _renderLibError(target, res.message || '请检查 QAS 配置', r.ms);
      else showToast('刷新失败：' + (res.message || 'QAS 返回异常') + '（显示的是缓存数据）', false);
      return;
    }
    // 丢弃「无名条目」：name 为空的条目既渲染不出有意义的卡片（标题/aria-label 全空），
    // 也无法按条目名删除（OpenList 侧 /fs/remove 认的就是条目名，空名会被后端拒绝）。
    // 与其渲染一张点了删不掉的空壳卡，不如不显示 —— 并且在下方的计数里说清楚
    // 「有 N 条无法显示」，避免用户以为数据少了。
    var rawEntries = res.entries || [];
    var entries = rawEntries.filter(function(it){ return it && String(it.name || '').trim(); });
    libHiddenNoName = rawEntries.length - entries.length;
    var meta = {source: res.source, fallback: res.fallback,
                fallback_reason: res.fallback_reason, fallback_hint: res.fallback_hint,
                fallback_kind: res.fallback_kind,
                fallback_backoff: res.fallback_backoff,
                fallback_backoff_left: res.fallback_backoff_left};
    libCache[target] = { ts: Date.now(), entries: entries, meta: meta };
    _libRememberRows(entries.length);   // 下次首帧骨架按真实条数占位，减少高度跳动
    _renderLibrary(target, entries, false, r.ms, meta);
  }catch(e){
    clearInterval(hintTimer);
    if(!hit) _renderLibError(target, e.message, Date.now() - t0);
  }
}

function _renderLibError(path, message, ms){
  var box = document.getElementById('libList');
  if(!box) return;
  libPath = path; libEntries = []; libSelected = {};
  // 换目录 / 失败重渲染：键盘状态一起重置。否则「在一个目录按 Shift 选了 30 项，
  // 进子目录再按 Shift+↓」会拿旧锚点去算新列表的下标 —— 选出来的范围莫名其妙。
  libFocusKey = null; libSelectAnchor = null; libRangeBase = null;
  renderBreadcrumb(path);
  var cnt = document.getElementById('libCount');
  if(cnt) cnt.textContent = '';
  // 失败态必须让**上一轮的回落结论一起消失**。
  //
  // 踩坑（实测）：徽标是挂在卡片标题行上的独立元素，渲染失败态时并没有清掉它。
  // 于是「先在某目录看到回落 → 再打开另一个目录、这次连 QAS 都失败了」的场景里，
  // 页面会同时显示「读取失败」和「QAS（OpenList 回落）· OpenList 请求超时…」，
  // 用户据此去改 OpenList 配置 —— 而真正挂了的是 QAS。失败态就应该只讲这一件事。
  libSource = 'qas';
  libFallback = false;
  libFallbackReason = '';
  libFallbackHint = '';
  libFallbackKind = '';
  libFallbackBackoff = false;
  libFallbackBackoffLeft = 0;
  libHiddenNoName = 0;
  _renderLibSourceBadge();
  box.innerHTML = '<div class="empty-state" style="padding:24px 20px">' +
    '<div class="empty-title">读取失败</div>' +
    '<div class="empty-desc">' + esc(message || '请检查 QAS 配置') + '</div>' +
    '<div class="empty-desc">已用 ' + (ms/1000).toFixed(1) + 's。网盘目录由 QAS 实时向夸克拉取，' +
    '若持续超时请检查 QAS 是否在线、夸克 Cookie 是否失效。</div>' +
    '<div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="loadLibrary(libPath, {force:true})">重试</button></div>' +
    '</div>';
  updateLibActionBar();
}

// 渲染一次目录（缓存命中与网络返回走同一条路径，避免两份 DOM 逻辑漂移）
function _renderLibrary(path, entries, fromCache, ms, source){
  var box = document.getElementById('libList');
  if(!box) return;
  libPath = path;
  libEntries = entries || [];
  // source === null 表示「只画内容，对数据源不做断言」（陈旧缓存先顶上的那一帧）。
  // 这种情况必须**原样保留**当前数据源状态，一个字都不改。
  //
  // 踩坑（本轮实测）：最初这里写成「无条件用 source 的值覆盖」，
  // 于是传 null 时把 libFallbackReason / Kind / Backoff 全清空了，
  // 而 libSource 因为 `if(source && source.source)` 的短判断反而留着旧值 ——
  // 结果得到一个自相矛盾的状态（source=qas 但 reason 为空），
  // 徽标既不是 OpenList 也不是「回落」，用户看到的就是一个没有来源的 QAS。
  if(source){
    if(source.source) libSource = source.source;
    if(typeof source.fallback === 'boolean') libFallback = source.fallback;
    libFallbackReason = source.fallback_reason || '';
    libFallbackHint = source.fallback_hint || '';
    libFallbackKind = source.fallback_kind || '';
    libFallbackBackoff = !!source.fallback_backoff;
    libFallbackBackoffLeft = Number(source.fallback_backoff_left || 0) || 0;
  }
  libSelected = {};
  // 键盘状态同理：DOM 每次重渲染都是全新的节点，focus/anchor 的 key 必须重新建立
  libFocusKey = null; libSelectAnchor = null; libRangeBase = null;
  libEntries.forEach(function(it, i){
    it._key = it.fid != null ? String(it.fid) : ('p' + i);
    // 条目「所属目录」在**渲染时就固定下来**（= 它真实被列出来的那个目录）。
    // 删除时若用当时的 libPath，就等于假设「渲染用的目录」与「删除时的目录」
    // 一直是同一个 —— 一旦中间发生过目录切换（比如用户开了第二个页面/标签，
    // 或者某条路径把 libPath 改了却没重渲染），这个假设就不成立，
    // 而删除是不可逆的：宁可在数据里记死这个目录。
    it._dir = path;
  });
  renderBreadcrumb(libPath);
  var upBtn = document.getElementById('libUpBtn');
  if(upBtn) upBtn.disabled = libAtRoot(libPath);
  var cnt = document.getElementById('libCount');
  if(cnt){
    var parts = [];
    if(libEntries.length) parts.push('共 ' + libEntries.length + ' 项');
    if(typeof ms === 'number' && ms >= 0) parts.push('耗时 ' + (ms/1000).toFixed(1) + 's');
    if(fromCache) parts.push('缓存');
    // 有条目因为「没有名字」被丢弃时明说，否则「怎么少了几项」无从解释。
    if(libHiddenNoName > 0) parts.push('另有 ' + libHiddenNoName + ' 项无名称未显示');
    cnt.textContent = parts.join(' · ');
  }
  _renderLibSourceBadge();
  if(!libEntries.length){
    box.innerHTML = '<div class="empty-state" style="padding:24px 20px"><div class="empty-title">空目录</div></div>';
    updateLibActionBar();
    return;
  }
  libEntries.sort(function(a,b){
    if(a.dir !== b.dir) return a.dir ? -1 : 1;
    return String(a.name).localeCompare(String(b.name), 'zh');
  });
  // 展示形态：布局（card/list）与密度（comfortable/compact）一起生效。
  // 这里**不**在缺省时重读 localStorage（`libView || libViewMode()` 那种写法）：
  // 状态初始化已由 _migrateLibPrefs() 在 loadLibraryPage 里统一完成，
  // 读两次存储会出现「刚切的密度被旧值盖回去」的竞态。
  _updateLibViewBtn();
  _updateLibDensityBtn();
  box.innerHTML = (libView === 'card') ? _libCardHtml() : _libTableHtml();
  updateLibActionBar();
}

// ---------- 列表视图（保留原有紧凑表格，随时可切回） ----------
function _libTableHtml(){
  var html = '<div class="lib-table">';
  // 列表视图的表头与卡片视图保持同一套语义：名称 / 大小 / 修改时间（issue #27 跟进）。
  html += '<div class="lib-row lib-head"><span class="lib-cb"></span><span>名称</span><span>大小</span><span>修改时间</span><span></span></div>';
  libEntries.forEach(function(it){
    // data-key 必须带上：键盘逻辑全靠它把 DOM 行与 libEntries 里的条目对上，
    // 缺了它 findEntry 找不到条目、勾选静默失效（本轮实测：列表视图里
    // 按空格「看起来有反应但什么都没选」就是这个原因）。
    html += '<div class="lib-row" data-key="' + esc(it._key) + '" tabindex="0"' +
      ' onclick="libCardClick(' + jsArg(it._key) + ', event)"' +
      ' onkeydown="libRowKeydown(' + jsArg(it._key) + ', event)">' +
      // 行本身就带 tabindex/onclick：键盘用户不必逐个 Tab 进勾选框，
      // Shift/Ctrl+点击的范围与增选语义也与卡片视图完全一致。
      '<span class="lib-cb"><input type="checkbox" aria-label="选择 ' + esc(it.name) + '" onchange="toggleLibSelect(' + jsArg(it._key) + ', this.checked)"></span>' +
      '<span class="lib-name">' +
        (it.dir
          ? '<a href="javascript:void(0)" onclick="enterDir(' + jsArg(it.name) + ')"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:inline;vertical-align:-3px;margin-right:4px"><use href="#icon-folder"/></svg>' + esc(it.name) + '</a>'
          : esc(it.name)) +
      '</span>' +
      '<span class="lib-sub">' + (it.dir ? '—' : fmtSize(it.size)) + '</span>' +
      // 修改时间：单独一格 + title，便于扫视/复制（与卡片视图同一格式）
      '<span class="lib-sub lib-sub-time" title="修改时间 ' + esc(fmtTs(it.updated_at)) + '">' + fmtTs(it.updated_at) + '</span>' +
      '<span class="lib-ops"><button class="btn btn-sm btn-outline" onclick="deleteOne(' + jsArg(it._key) + ')">删除</button></span>' +
      '</div>';
  });
  html += '</div>';
  return html;
}

// ---------- 卡片视图（等高紧凑卡，issue #27 观感重做） ----------
//
// 排版取舍（issue #27 两轮反馈的真因）：
//   1. 第一版：整张卡做成 2:3 海报框 → 有海报的卡很高、无海报的卡一大块纯色空框，
//      行内高矮参差、大片空白；
//   2. 第二版：缩略块改成固定 16:9 想压平高度，但「有些卡有图、有些卡没图」本身
//      就是观感噪音 —— 灰块与海报混排，越大的目录越花；
//   3. 现在：**整块缩略图去掉**。卡片 = 「一行图标 + 名称 + 元信息」，
//      文件夹用文件夹图标、文件用文档图标，所有卡天然等高、无空框、无占位块
//      （图标本身的形状与尺寸见 _libCardIconSvg，issue #47 修了「图标异常」）。
//
// 卡片内所有元素都在底部信息行里：
//   [勾选框] [图标 + 名称 / 元信息] [删除图标]
// 「进入目录」仍然是文件夹卡标题上的链接（图标与标题同在链接里）。
function _libCardHtml(){
  // 条目多时给网格一个高度上限（用户可以在卡片内滚动，不用被一堆卡推着翻页面）。
  // 记录数量用于走查断言：行数多而高度未被限制 = 会变成「一屏全是卡」。
  var box = document.getElementById('libList');
  if(box) box.setAttribute('data-lib-count', String(libEntries.length));
  // 密度落在网格类名上（.lib-grid-compact）：列宽由 CSS 决定，
  // 卡片模板本身**一行为一档**，不在 JS 里拼样式 —— 走查断言要的是渲染后数值。
  var html = '<div class="lib-grid' + (libDensity === 'compact' ? ' lib-grid-compact' : '') + '" id="libGrid">';
  libEntries.forEach(function(it){
    html += it.dir ? _libDirCardHtml(it) : _libFileCardHtml(it);
  });
  html += '</div>';
  return html;
}

// 卡片类型图标（issue #47「文件缩略图图标异常」）。
//
// 原来这里画的是 `viewBox="0 0 24 24"` 的图标，与旁边的勾选框 / 删除按钮相比显得
// 「小得离谱」—— 同类控件一行里尺寸不齐，观感就是「渲染错了」。本轮做两件事：
//   1. 尺寸按内联像素给足（18 → 20），与 18px 勾选框、14px 删除图标形成层级；
//   2. 图标内联在卡片模板里，**不再走 `<use href="#icon-…">`**：`<use>` 依赖
//      文档里的 sprite `<defs>`，一旦宿主页面没有（或 id 被改名），图标会静默消失，
//      表现正是「图标异常」。
//
// 两个图形都画在 `0 0 20 20` 视口里（`24` 视口配 16px 内联尺寸会让笔画发虚）：
//   - 文件夹：一个实心「夹子」轮廓 + 浅色（.lib-card-icon-fill）填充，缩到 20px
//     仍能一眼看出是文件夹；
//   - 文件：一页纸 + 折角，**不是毛片** —— 「文件」不等于「影片」，网盘目录里
//     也有字幕、图片、压缩包，用胶片图标会让用户误判类型（issue #47 的截图上
//     两个 9.5 KB / 21.4 GB 的动画文件被画成电影胶片，就是这个问题）。
function _libCardIconSvg(kind){
  var common = 'width="20" height="20" viewBox="0 0 20 20" fill="none" '
    + 'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"';
  if(kind === 'dir'){
    return '<svg ' + common + '>'
      + '<path class="lib-card-icon-fill" d="M2 6a2 2 0 0 1 2-2h3.2a2 2 0 0 1 1.6.8l.9 1.2H16a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z"/>'
      + '</svg>';
  }
  return '<svg ' + common + '>'
    + '<path d="M11.5 2.5H6A2 2 0 0 0 4 4.5v11a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7Z"/>'
    + '<path d="M11.5 2.5V7H16"/>'
    + '<path d="M7.5 11.5h5"/><path d="M7.5 14h3"/>'
    + '</svg>';
}

// 卡片外壳：勾选框 / 删除按钮 / 标题分别渲染。
function _libCardShell(it, kind){
  var nameHtml = it.dir
    ? '<a class="lib-card-title lib-card-dirlink" href="javascript:void(0)" onclick="enterDir(' + jsArg(it.name) + ')" title="进入 ' + esc(it.name) + '">' + esc(it.name) + '</a>'
    : '<span class="lib-card-title" title="' + esc(it.name) + '">' + esc(it.name) + '</span>';
  // tabindex="0"：让每一行都能被 Tab 到达（只读屏幕阅读器/纯键盘用户），
  // 也是「方向键移动当前行」的前提；点击快捷键走 onclick（见 libCardClick），
  // 勾选框自己不再绑点击键，避免「点一下触发两次」。
  return '<div class="lib-card lib-card-' + kind + '" data-key="' + esc(it._key) + '" data-name="' + esc(it.name) + '"' +
      ' tabindex="0" onclick="libCardClick(' + jsArg(it._key) + ', event)" onkeydown="libRowKeydown(' + jsArg(it._key) + ', event)">' +
    '<label class="lib-card-cb" title="选择该' + (it.dir ? '文件夹' : '文件') + '">' +
      '<input type="checkbox" aria-label="选择 ' + esc(it.name) + '" onchange="toggleLibSelect(' + jsArg(it._key) + ', this.checked)">' +
    '</label>' +
    '<span class="lib-card-icon" aria-hidden="true">' +
      _libCardIconSvg(kind) +
    '</span>' +
    '<span class="lib-card-name-col">' +
      nameHtml +
      _libMetaHtml(it) +
    '</span>' +
    '<button class="lib-icon-btn" title="删除" aria-label="删除 ' + esc(it.name) + '" onclick="deleteOne(' + jsArg(it._key) + ')">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><use href="#icon-trash"/></svg>' +
    '</button>' +
  '</div>';
}

// 元信息（issue #27 跟进：用户要求「添加显示修改时间」）。
//
// 有图的那版里「大小 · 修改时间」被挤在同一行；去掉图以后卡片虽变宽，
// 但两段都是数字、扫视时区分不出哪一段是时间。现在拆成**两行**：
//   - 第一行：文件大小（文件夹则显示「文件夹」）；
//   - 第二行：修改时间，带时钟图标 + 「修改」前缀。
//
// 踩坑（本轮实测）：卡片只有 180~240px 宽，`修改 2026-09-17 20:04`（约 112px）
// 加上图标与留白后**装不下**，实测 1440 与 2560 视口下时间仍被省略号截成
// `修改 2025-0…` —— 用户看到的还是「没有修改时间」。因此时间行做两层：
//   - `.lib-time-full`（yyyy-mm-dd hh:mm，移动端 1 列时够宽，可完整显示）；
//   - `.lib-time-short`（mm-dd hh:mm，紧凑卡下显示）。
// 两者由 CSS 容器查询按**实际可用宽度**取舍（@container timefit，见 main.css），
// 容器查询不支持时默认显示完整格式；完整值同时放在 title 里，任何情况下都能看到。
function _libMetaHtml(it){
  if(it.dir) return '<span class="lib-card-meta">文件夹</span>';
  var full = fmtTs(it.updated_at);
  var short = fmtTsShort(it.updated_at);
  return '<span class="lib-card-meta">' + esc(fmtSize(it.size)) + '</span>' +
    '<span class="lib-card-time" title="修改时间 ' + esc(full) + '">' +
      '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><use href="#icon-clock"/></svg>' +
      '<span class="lib-time-text lib-time-full">修改 ' + esc(full) + '</span>' +
      '<span class="lib-time-text lib-time-short">修改 ' + esc(short) + '</span>' +
    '</span>';
}

// 单行拼接口径（「大小 · 修改时间」）：列表视图与提示文案复用，保证全站时间格式一致。
function _libMetaText(it){
  if(it.dir) return '文件夹';
  var bits = [fmtSize(it.size)];
  var ts = fmtTs(it.updated_at);
  if(ts && ts !== '—') bits.push(ts);
  return bits.join(' · ');
}

function _libDirCardHtml(it){
  return _libCardShell(it, 'dir');
}

function _libFileCardHtml(it){
  return _libCardShell(it, 'file');
}

function invalidateLibCache(path){
  delete libCache[path == null ? libPath : path];
}

// 刷新按钮：强制绕过缓存
function refreshLibrary(){
  invalidateLibCache(libPath);
  loadLibrary(libPath, {force:true});
}

function renderBreadcrumb(path){
  var el = document.getElementById('libBreadcrumb');
  if(!el) return;
  var root = libRoot();
  var p = String(path || '/');
  // 相对根目录的片段：根目录自身不显示前缀段
  var rel = p;
  if(root !== '/' && (p === root || p.indexOf(root + '/') === 0)){
    rel = p.slice(root.length);
  }else if(root === '/'){
    rel = p;
  }else{
    rel = p;   // 越界路径（理论上被 libGoUp/libEnterDir 拦下）仍按原文显示
  }
  var parts = rel.split('/').filter(Boolean);
  var rootLabel = root === '/' ? '根目录' : esc(root);
  var html = '<a href="javascript:void(0)" onclick="loadLibrary(' + jsArg(root) + ')">' + rootLabel + '</a>';
  var acc = root === '/' ? '' : root;
  parts.forEach(function(seg){
    acc += '/' + seg;
    html += ' <span class="lib-sep">/</span> <a href="javascript:void(0)" onclick="loadLibrary(' + jsArg(acc) + ')">' + esc(seg) + '</a>';
  });
  el.innerHTML = html;
}

function enterDir(name){
  var base = libPath === '/' ? '' : libPath;
  loadLibrary(base + '/' + name);
}

function libGoUp(){
  if(libAtRoot(libPath)) return;   // 媒体库根目录即下界，不允许再往上（issue #27）
  var parts = libPath.split('/').filter(Boolean);
  parts.pop();
  var next = '/' + parts.join('/');
  if(next === '') next = '/';
  // 若上级已越过配置根目录，则停在根目录
  var root = libRoot();
  if(root !== '/' && next !== root && next.indexOf(root + '/') !== 0) next = root;
  loadLibrary(next);
}

function findEntry(key){
  for(var i = 0; i < libEntries.length; i++){
    if(libEntries[i]._key === key) return libEntries[i];
  }
  return null;
}

function toggleLibSelect(key, on){
  var it = findEntry(key);
  if(!it) return;
  if(on) libSelected[key] = it; else delete libSelected[key];
  updateLibActionBar();
}

function updateLibActionBar(){
  var bar = document.getElementById('libActionBar');
  var n = Object.keys(libSelected).length;
  if(bar) bar.style.display = libEntries.length ? 'flex' : 'none';
  var allBar = document.getElementById('libSelectAllBar');
  if(allBar) allBar.style.display = libEntries.length ? 'flex' : 'none';
  var cnt = document.getElementById('libSelCount');
  if(cnt) cnt.textContent = n ? '已选 ' + n + ' 项' : '';
  var btn = document.getElementById('libDelBtn');
  if(btn) btn.disabled = n === 0;
  var all = document.getElementById('libSelAll');
  if(all){
    all.checked = n > 0 && n === libEntries.length;
    all.indeterminate = n > 0 && n < libEntries.length;
  }
}

// ---------- 键盘选择（issue #53） ----------
//
// 用户的原话：「我希望在使用电脑进行选中时，可以使用电脑的快捷键进行选择。
// 当前的选中效率太低了。没有批量全选和小部分范围的选择快捷键」。
//
// 这里按「文件管理器」的通行约定实现，而不是另发明一套：
//   * 列表获得焦点后 ↑/↓ 移动当前行，并用**虚线框**标出来 —— 没有「锚点」
//     就谈不上「范围选择」，这也正是原来「不知道选到第几个」的成因；
//   * Ctrl/⌘+A 全选、Esc 清空、Space 切换当前行；
//   * Shift+↑/↓ 从锚点连选一段（「小部分范围选择」）；
//   * Shift+点击 / Ctrl+点击 与桌面端一致（锚点 + 范围 / 逐个增减）。
//
// 刻意不做的事：
//   1. 不做「框选（marquee）」：网格会被拖拽手势与文本选择干扰，收益却有限；
//   2. Ctrl/⌘+Shift+A 这类「取消全选」不做 —— Ctrl+A + Esc 已经够用，
//      多一个组合键就多一处没人记得住的行为。
//   3. 方向键**不**跨列跳行（网格是 auto-fill，列数随视口变，跨列跳会跳错），
//      只按 DOM 顺序（= 展现顺序）上下移动。

function libGridEl(){
  return document.getElementById('libGrid');
}
// 当前视图的行元素（卡片视图 = #libGrid 里的 .lib-card；列表视图 = .lib-table 里的
// 数据行）。**两种视图必须走同一套键盘逻辑** —— 否则「我切成列表视图后快捷键就
// 不好使了」是必然的。列表视图没有 #libGrid，所以要回落到 .lib-table。
function libRowEls(){
  var g = libGridEl();
  var scope = g || document.querySelector('#libList .lib-table');
  if(!scope) return [];
  var rows = scope.querySelectorAll('.lib-card, .lib-row:not(.lib-head)');
  return Array.prototype.slice.call(rows);
}

// 当前聚焦行在**展现顺序**里的下标；没聚焦过时回落到 -1。
// 网格里行被渲染的顺序已由 _renderLibrary 排好（目录在前、名称升序），
// 所以这里直接用 DOM 顺序，不再自己算一遍排序（两份排序必然漂移）。
function _libFocusIdx(rows){
  var k = libFocusKey;
  if(k == null) return -1;
  for(var i = 0; i < rows.length; i++){
    if(rows[i].getAttribute('data-key') === k) return i;
  }
  return -1;
}

function libFocusRow(i, opts){
  opts = opts || {};
  var rows = libRowEls();
  if(!rows.length) return;
  if(i < 0) i = 0;
  if(i > rows.length - 1) i = rows.length - 1;
  libFocusKey = rows[i].getAttribute('data-key');
  // 「当前行」必须**看得见**：只把下标记在变量里，用户就还是「不知道选到第几个」
  // —— 这正是 issue #53 说「效率太低」的根因之一。样式见 .lib-current（虚线框）。
  // 每次移动都全量重刷（n 很小，且不用 querySelector 逐个比）：上一次的标记必须
  // 被清掉，否则会留下一条「幽灵当前行」，Shift 连选时看起来像选了两段。
  for(var j = 0; j < rows.length; j++){
    if(j === i) rows[j].classList.add('lib-current');
    else rows[j].classList.remove('lib-current');
  }
  if(opts.scroll !== false){
    // 卡片网格是内部滚动容器（max-height:560px），滚动到「最近」而不是居中：
    // 居中会让列表在按方向键时频繁整屏跳动。
    try{ rows[i].scrollIntoView({block:'nearest'}); }catch(e){ rows[i].scrollIntoView(false); }
  }
}

// 把 libSelected 的状态**回写到渲染层**（勾选框 DOM 属性）。
//
// 踩坑（本轮实测）：原来这里只改 libSelected，不打勾选框 —— 因为「鼠标点」时
// 浏览器会自己翻转 checkbox（所以不需要写）。但**键盘来的范围选择不会经过
// 浏览器**：内部状态说「选了 3 项」、操作条也显示「已选 3 项」，
// 而屏幕上 12 个勾选框全是空的。用户看到的就是「按了 Shift+↓ 什么都没发生」
// —— 比没实现还糟。所有「非浏览器触发」的选择变更都必须经过这里。
function _libSyncCheckboxes(rows){
  (rows || libRowEls()).forEach(function(r){
    var box = r.querySelector('input[type=checkbox]');
    if(box) box.checked = !!libSelected[r.getAttribute('data-key')];
  });
}

function _libSelectRow(i, rows, keys){
  var r = rows[i];
  if(!r) return;
  libSelected[r.getAttribute('data-key')] = findEntry(r.getAttribute('data-key'));
  if(keys) keys[String(i)] = 1;
}

// 选中从 anchor 到 focus 的闭区间（「小部分范围选择」）。
//
// 范围选择必须**可增可减**，但「已经选好的东西」也不能因为一次 Shift 就丢掉。
// 这两件事靠一个量同时表达，就是 libRangeBase（上一次范围选择的状态）：
//
//   本次选区 = ( 基线选择里那些**落在本次范围之外**的项 ) ∪ 本次范围
//
// 「基线选择」= 上一次范围选择时记录的「范围之外的选择」= 用户自己攒下的那部分。
// 由此得到与文件管理器一致的行为：
//   * 全新 Shift（没有 libRangeBase）→ 直接「既有选择 ∪ 本次范围」，
//     先 Ctrl+点击点出来的几项不会被吞掉；
//   * Shift+↓ 一次次往下 → 范围右端不断外扩，左边那几项仍在；
//   * Shift+↑ 往回收 → 范围右端缩回，只有「外扩出来的」部分被移除，
//     自己的选择与锚点端**都留着**（不会像纯并集那样只能扩不能缩）。
//
// 踩坑（本轮实测两轮）：
//   1. 纯并集 → Shift+↓ 之后 Shift+↑ 什么都不发生，用户只能重来；
//   2. 快照写成「本次 libSelected - 本次范围」→ 把上一次范围的残留留下来，
//      Shift+↓ ×2 只选出 2 项（应为 3）。正确顺序是**先算 next，再取
//      next - 本次范围**，否则减掉的是旧范围、留下来的却是旧选区。
function _libSelectRange(anchor, focus, rows){
  var a = Math.min(anchor, focus), b = Math.max(anchor, focus);
  var next = {};
  // 1. 生成态：本次范围里的行一律选上
  for(var j = a; j <= b; j++){
    var k1 = rows[j].getAttribute('data-key');
    next[k1] = findEntry(k1);
  }
  // 2. 保留态：上一轮基线里、**落在本次范围之外**的选择。
  //
  //    这里刻意不是「保留 libRangeBase.base 的全部」：base 里可能包含
  //    「上一轮范围扩出去的行」，那些正是 Shift+↑ 要收回去的部分。
  //    判据用「行下标是否落在本次 [a,b] 之外」——
  //    「范围外」是由**位置**决定的，不是由「来自哪一次快照」决定的。
  //    （踩坑：曾经只遍历 base.prevRange 去筛，结果用户 Ctrl+点击攒下的选择
  //    既不在 prevRange 里、也没被保留，静默丢掉。）
  var keep = libRangeBase ? libRangeBase.base : {};
  var prevRange = libRangeBase ? libRangeBase.prevRange : {};
  var next2 = {};
  Object.keys(keep).forEach(function(key){
    var i = _libRowIdxOfKey(key, rows);
    if(i >= 0 && i >= a && i <= b) return;         // 落在本次范围里，由范围决定
    if(prevRange[key]) return;                     // 属于上一轮范围：本次不再保留
    next2[key] = findEntry(key) || keep[key];
  });
  // 3. 第一次 Shift（无基线）：保留当前所有既有选择（可能是 Ctrl+点击攒出来的）
  if(!libRangeBase){
    for(var k = 0; k < rows.length; k++){
      var key = rows[k].getAttribute('data-key');
      if(next[key]) continue;
      if(libSelected[key]) next2[key] = findEntry(key) || libSelected[key];
    }
  }
  Object.keys(next2).forEach(function(key){
    if(!next[key]) next[key] = next2[key];
  });
  // 4. 记下基线：本次范围 + 「本次范围之外、仍需保留的选择」（下次扩/缩的基数）
  var snapRange = {};
  for(var j2 = a; j2 <= b; j2++) snapRange[rows[j2].getAttribute('data-key')] = 1;
  var baseSnap = {};
  Object.keys(next).forEach(function(key){
    if(!snapRange[key]) baseSnap[key] = next[key];
  });
  libRangeBase = { start:a, end:b, prevRange:snapRange, base:baseSnap };
  libSelected = next;
  _libSyncCheckboxes(rows);
  updateLibActionBar();
}

// 行 key → 行下标（-1 = 不在当前列表里）
function _libRowIdxOfKey(key, rows){
  for(var i = 0; i < (rows || []).length; i++){
    if(rows[i].getAttribute('data-key') === String(key)) return i;
  }
  return -1;
}

function libSelectAll(){
  var rows = libRowEls();
  if(!rows.length) return;
  // 复用既有入口：它负责「就地同步每行勾选框」与操作条计数
  toggleLibSelectAll(true);
  var all = document.getElementById('libSelAll');
  if(all) all.checked = true;
  libSelectAnchor = rows.length ? 0 : null;
  showToast('已全选本目录 ' + rows.length + ' 项（Esc 取消选择）', true);
}

function libClearSelection(){
  toggleLibSelectAll(false);
  var all = document.getElementById('libSelAll');
  if(all) all.checked = false;
  // 锚点必须一起清掉，否则再按 Shift+↑ 会从「上一次的位置」重新选出一段，
  // 而用户刚看到的是「已清空」。范围基线同理（它记的是「上一次选区」）。
  libSelectAnchor = null; libRangeBase = null;
}

// 鼠标：Shift+点击 = 从锚点连选到该行；Ctrl/⌘+点击 = 逐个增减。
// 普通点击沿用原行为（点一下勾一个），锚点随之移动。
function libCardClick(key, ev){
  ev = ev || {};
  var rows = libRowEls();
  var idx = -1;
  for(var i = 0; i < rows.length; i++){
    if(rows[i].getAttribute('data-key') === String(key)){ idx = i; break; }
  }
  if(idx < 0) return;
  var shift = !!ev.shiftKey;
  var multi = !!(ev.ctrlKey || ev.metaKey);
  libFocusKey = String(key);
  if(shift && libSelectAnchor != null){
    // 范围选择时不改锚点：连续多次 Shift+点击可以反复调整选区的下界
    // （这就是文件管理器里「点了不准，再点一下」的交互）。
    libFocusRow(idx, {scroll:false});
    _libSelectRange(libSelectAnchor, idx, rows);
    return;
  }
  // 单选 / 多选切换：浏览器在被标记为「普通点击」时会自行翻转 checkbox，
  // 所以这里只同步状态，不再手动改 checked（否则会翻两次）。
  var it = findEntry(key);
  var on = multi ? !libSelected[key] : true;
  if(on && it) libSelected[key] = it; else delete libSelected[key];
  // 新锚点意味着「新的一段范围」：旧基线必须作废，否则下一次 Shift 会拿
  // 上一次的范围去减，减出谁也预料不到的结果。
  libRangeBase = null;
  // Ctrl/⌘+点击走的是 libListMouseDown 的 preventDefault 分支（不触发原生翻转），
  // 所以这里同样得自己回写勾选框；普通点击则「浏览器已经翻好了」，回写是幂等的。
  _libSyncCheckboxes(rows);
  updateLibActionBar();
  libSelectAnchor = idx;
}

// 行上的 keydown：Shift+↑↓ / Ctrl+↑↓ 属于「范围选择」，必须在这里拦下来：
// 卡片里的 checkbox 一旦拿到焦点，浏览器的 Space 会自己翻转它，
// 而我们在 document 上再处理一次就会翻两次（勾了又取消）。
// （列表视图的 <a> 同样会吞掉空格键，所以两种视图都拦。）
function libRowKeydown(key, ev){
  if(!ev) return;
  if(ev.key === 'ArrowUp' || ev.key === 'ArrowDown'){ _libKeydown(ev); return; }
  if(ev.key === ' ' || ev.key === 'Spacebar'){ _libKeydown(ev); }
}

// 返回 true = 这个键已经被媒体库的选择逻辑吃掉了（init.js 据此提前 return）。
// 注意：**不能**只靠「有没有副作用」来判断，否则 Ctrl+A 会连带触发后面的
// 全局快捷键分支（历史上这类「两个处理器都吭声」的问题很难查）。
function _libKeydown(e){
  if(currentTab !== 'library') return false;
  var rows = libRowEls();
  if(!rows.length) return false;
  var t = e.target || {};
  var tag = t.tagName || '';
  var isCb = tag === 'INPUT' && t.type === 'checkbox';   // 勾选行上的原生框
  var idx = _libFocusIdx(rows);
  var focus = idx < 0 ? 0 : idx;
  var anchor = (libSelectAnchor == null || libSelectAnchor < 0 || libSelectAnchor >= rows.length)
    ? focus : libSelectAnchor;
  var k = e.key, lower = String(k || '').toLowerCase();
  var mod = !!(e.ctrlKey || e.metaKey);

  // Ctrl/⌘+A：全选（必须抢在浏览器「选中页面文字」之前 preventDefault）
  if(mod && lower === 'a'){
    e.preventDefault();
    libSelectAll();
    return true;
  }
  // Esc：清空选择。确认弹窗的 Esc 由 confirm.js 处理，且它的遮罩此时在最上层，
  // 这里只在「有选择」时动手，避免与弹窗抢键。
  if(k === 'Escape' && Object.keys(libSelected).length){
    e.preventDefault();
    libClearSelection();
    return true;
  }
  // Ctrl/⌘+↑↓：移动当前行但不改变选择（文件管理器里用来「看看但不选」）
  if(mod && (k === 'ArrowUp' || k === 'ArrowDown')){
    e.preventDefault();
    var step = (k === 'ArrowDown') ? 1 : -1;
    libFocusRow(focus + step);
    libSelectAnchor = libFocusRowIdx(rows);
    libRangeBase = null;      // 移动了当前行 = 开始新一段范围
    return true;
  }
  if(k === 'ArrowUp' || k === 'ArrowDown'){
    e.preventDefault();
    var ns = (k === 'ArrowDown') ? focus + 1 : focus - 1;
    if(ns < 0 || ns > rows.length - 1) return true;   // 到边界就停住（不回绕）
    libFocusRow(ns);
    var ni = _libFocusIdx(rows);
    // Shift+↑↓：从锚点连选一段 —— 「小部分范围选择」的主入口。
    // 注意：**Shift 连选期间锚点不能跟着焦点跑**，否则每按一次 Shift+↓
    // 范围起点都往后挪一格，按住 5 次只选出 2 行（本轮实测就是这个现象）。
    // 锚点只在「非 Shift 的移动/点击」时更新。
    if(e.shiftKey){
      _libSelectRange(anchor, ni, rows);
    }else{
      libSelectAnchor = ni;
    }
    return true;
  }
  // Space：切换当前行。Ctrl/⌘+Space 与 Space 有区别才不会有意外：
  // 前者永不取消已勾选项（用于「先把范围的起点建立起来」），后者是真切换。
  if(k === ' ' || k === 'Spacebar'){
    // 焦点落在原生 checkbox 上时，浏览器自己会翻，这里跳过（否则翻两次）
    if(isCb && !mod) return false;   // 交给浏览器的原生行为，别翻两次
    e.preventDefault();
    var cur = rows[focus];
    var key = cur.getAttribute('data-key');
    toggleLibSelect(key, !libSelected[key]);
    _libSyncCheckboxes(rows);
    libSelectAnchor = focus;
    return true;
  }
  return false;
}

// 焦点行下标（渲染后重新查一遍，避免拿着过期下标）
function libFocusRowIdx(rows){
  return _libFocusIdx(rows || libRowEls());
}

// 列表容器上的 mousedown：把「Ctrl/⌘+点击、Shift+点击」标成非拖动，
// 否则 <label> 会因为「可能是在拖动文字」而只给焦点、不翻转勾选框。
function libListMouseDown(e){
  if(!e || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.button !== 0) return;
  var t = e.target;
  while(t && t !== document){
    if(t.classList && (t.classList.contains('lib-card') || t.classList.contains('lib-row'))){
      e.preventDefault();
      return;
    }
    t = t.parentNode;
  }
}

function toggleLibSelectAll(on){
  libSelected = {};
  if(on){
    libEntries.forEach(function(it){ libSelected[it._key] = it; });
  }
  // 就地同步每行的勾选框，避免整表重渲染把当前选择清空
  libEntries.forEach(function(it){
    var cb = document.querySelector('input[aria-label="选择 ' + _cssQuote(it.name) + '"]');
    if(cb) cb.checked = !!libSelected[it._key];
  });
  updateLibActionBar();
}

// 供 querySelector 拼接属性选择器用：转义引号与反斜杠
function _cssQuote(v){
  return String(v == null ? '' : v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// ---------- 删除（含历史同步） ----------

// 文件名通常带后缀/清晰度（如 电影A.2024.2160p.mkv），历史里存的是作品名。
// 这里先剥掉扩展名再去匹配；匹配不到时后端会如实回报「历史无匹配」，不会误删。
function guessTitle(name){
  if(!name) return '';
  return String(name).replace(/\.[a-zA-Z0-9]{2,5}$/, '').trim();
}

async function doDelete(entries, label){
  if(!entries.length) return;
  // 删除前的一致性校验：OpenList 侧按「目录 + 条目名」删，两者的组合必须
  // 唯一指向用户看到的那一条。这里挡的是「条目拿到的目录与当前浏览目录不一致」
  // 的拼接串味 —— 它不会报错，只会静静删掉别的东西。
  var badDir = entries.filter(function(e){
    return e && e.dir != null && e.dir !== libPath;
  });
  if(badDir.length){
    showToast('删除已取消：选中条目来自 ' + badDir[0].dir +
      '，与当前目录 ' + libPath + ' 不一致，请重新进入该目录后再删除', false);
    return;
  }
  var sync = document.getElementById('syncHistoryChk');
  var syncHistory = !sync || sync.checked;
  var extra = syncHistory ? '\n同时会清理 DBAuto 中对应的转存历史（匹配不到则跳过）。' : '';
  // 联动删任务必须在确认框里复述（issue #57）：删任务不可逆，
  // 不能让用户在「确认删除文件」的弹窗里顺手删掉一批任务配置。
  var syncTasksExtra = _syncTasksFromLibExtra(entries);
  if(syncTasksExtra.syncTasks){
    extra += '\n\n同时会删除同名（任务名与文件名互相包含）的 QAS 转存任务配置' +
      (syncTasksExtra.pathFilter ? '，且仅限保存路径含「' + syncTasksExtra.pathFilter + '」的任务' : '') +
      '。任务配置删除后不可恢复。';
  }
  // 必须检查返回值：取消/遮罩/ESC 都是 false，此时不能删除（issue #27 严重问题）
  var confirmed = await confirmThen('确认删除', '将从夸克网盘删除 ' + label + '。' + extra, '删除', '取消');
  if(!confirmed) return;

  var btn = document.getElementById('libDelBtn');
  if(btn) btn.classList.add('loading');
  try{
    var payload = {
      entries: entries,
      sync_history: syncHistory,
      // 谁列出来的条目就交给谁删：OpenList 认条目名、QAS 认 fid。
      // 跨源转换需要猜父目录，猜错就是删掉别的东西（后端也会按 source 校验字段）。
      source: libSource
    };
    // 删除范围开关（issue #57）：勾了才把「同步删同名任务」的意图发给后端。
    // 默认关时连字段都不发 —— 静默删任务是最贵的事故（要一条条重建）。
    var syncExtra = _syncTasksFromLibExtra(entries);
    if(syncExtra.syncTasks){
      payload.sync_tasks = true;
      if(syncExtra.pathFilter) payload.sync_tasks_path_filter = syncExtra.pathFilter;
      payload.sync_tasks_names = syncExtra.names;
    }
    var res = await apiPost('/api/qas/delete', payload);
    if(res.success){
      showToast('已删除 ' + res.deleted + ' 项' + (res.history_synced ? '，同步清理历史 ' + res.history_synced + ' 条' : '') +
        _tasksResultMsg(res, syncExtra.syncTasks), true);
    }else if(res.deleted > 0){
      showToast('部分成功：删除 ' + res.deleted + '，失败 ' + res.failed, false);
    }else{
      showToast(res.message || '删除失败', false);
    }
  }catch(e){
    showToast('删除失败: ' + e.message, false);
  }finally{
    if(btn) btn.classList.remove('loading');
  }
  // 删除后列表必然变化，缓存作废再拉取
  invalidateLibCache(libPath);
  loadLibrary(libPath, {force:true});
}

function deleteOne(key){
  var it = findEntry(key);
  if(!it) return;
  doDelete([_libDeleteEntry(it)], it.name);
}

// 删除请求里的条目形态取决于数据源：
//   QAS 侧 → fid（QAS 用它反查夸克 fid），并带上 path 以便作品目录场景；
//   OpenList 侧 → 条目名 + 所在目录（OpenList 的 /fs/remove 认这一对）。
function _libDeleteEntry(it){
  var e = {name: it.name, title: guessTitle(it.name)};
  if(libSource === 'openlist'){
    // OpenList 侧的 /fs/remove 要的是「目录 + 条目名」这一对。目录必须与
    // **列目录时用的那个路径**严格一致：后端拿它作为删除目标目录，
    // 与列表数据来源不符时会删错对象。所以这里显式带上当前目录，
    // 并且顺序要紧 —— 见 doDelete 里对 `dir` 的一致性校验。
    e.dir = it._dir || libPath;
  }else{
    e.fid = it.fid;
    e.path = (libPath === '/' ? '' : libPath) + '/' + it.name;
  }
  return e;
}

function deleteSelected(){
  var keys = Object.keys(libSelected);
  if(!keys.length) return;
  var entries = keys.map(function(k){ return _libDeleteEntry(libSelected[k]); });
  doDelete(entries, '所选的 ' + entries.length + ' 项');
}

// ---------- 任务对账（issue #27 追问：「我同时需要管理夸克自动转存的自动任务条目的需求」） ----------
//
// 引入 OpenList 之后才有的能力：以前只能看到「QAS 里有哪些任务」，
// 看不到「这些任务在网盘上还有没有对应内容」，于是任务失效了也发现不了
// （每天空跑一次，或者在日志里报失效链接，但用户看不出是哪条任务的问题）。
//
// 三条设计取向：
//   1. **只读**：这里不给任何删除入口。巡检结论容易让人顺手点「清理」，
//      而「网盘上暂时没扫到」不等于「这条任务没用了」—— 自动清理会误删有效任务。
//      真要清理，用上面两条既有链路（都有确认弹窗）。
//   2. **区分「未核对」与「失效」**：savepath 落在扫描范围外的任务单独列，
//      不混进「失效」。否则用户会以为任务坏了，去删掉本来有效的配置。
//   3. **说清扫描范围**：目录数 / 层数超限时明确标注「未覆盖全部目录」，
//      不能让用户以为「没报出来 = 全都正常」。
async function runTaskAudit(){
  var box = document.getElementById('auditResult');
  var btn = document.getElementById('auditBtn');
  if(!box) return;
  if(btn){ btn.disabled = true; btn.classList.add('loading'); }
  box.innerHTML = '<div class="empty-state" style="padding:20px"><div class="empty-title">对账中...</div>' +
    '<div class="empty-desc">正在读取网盘目录并与 QAS 任务逐条核对（最多下钻 ' + AUDIT_HINT + '，不会修改任何东西）</div></div>';
  try{
    var r = await libFetch('/api/openlist/tasks/audit?path=' + encodeURIComponent(libRoot()));
    if(!r.ok){
      _renderAuditError(r.message);
      return;
    }
    var d = r.data || {};
    if(!d.success){
      _renderAuditError(d.message || '对账失败');
      return;
    }
    _renderAuditResult(d);
  }catch(e){
    _renderAuditError(e.message);
  }finally{
    if(btn){ btn.disabled = false; btn.classList.remove('loading'); }
  }
}

var AUDIT_HINT = '2 层目录';

function _renderAuditError(message){
  var box = document.getElementById('auditResult');
  var cnt = document.getElementById('auditCount');
  if(cnt) cnt.textContent = '';
  if(!box) return;
  box.innerHTML = '<div class="empty-state" style="padding:20px">' +
    '<div class="empty-title">对账失败</div>' +
    '<div class="empty-desc">' + esc(message || '请检查 OpenList 与 QAS 配置') + '</div>' +
    '<div class="empty-desc"><button class="btn btn-sm btn-outline" onclick="runTaskAudit()">重试</button></div>' +
    '</div>';
}

function _renderAuditResult(d){
  var box = document.getElementById('auditResult');
  var cnt = document.getElementById('auditCount');
  if(!box) return;
  var missing = d.missing || [];
  var unverified = d.unverified || [];
  var orphans = d.orphans || [];
  if(cnt){
    var bits = ['已核对 ' + (d.checked || 0) + ' 条任务', '扫描 ' + (d.scanned_dirs || 0) + ' 个目录'];
    if(missing.length) bits.push('失效 ' + missing.length);
    cnt.textContent = bits.join(' · ');
  }
  var html = '';
  // 扫描范围不完整时必须在最显眼处说明，否则「0 处问题」会被误读成「全都正常」
  if(d.truncated){
    html += '<div class="lib-tip" style="border-color:var(--orange);color:var(--text2)">' +
      '目录数超过巡检上限，本次**未覆盖全部目录**：不在结果里 ≠ 没有问题。' +
      '可在「设置 → 数据源」把媒体库根目录指向更具体的路径后再对账。</div>';
  }

  if(!missing.length && !unverified.length && !orphans.length){
    html += '<div class="empty-state" style="padding:20px"><div class="empty-title">未发现问题</div>' +
      '<div class="empty-desc">已核对的 ' + (d.checked || 0) + ' 条任务在网盘上都能找到对应内容。</div></div>';
    box.innerHTML = html;
    return;
  }

  if(missing.length){
    html += '<div class="audit-group audit-bad">' +
      '<div class="audit-group-title">任务失效 ' + missing.length + ' 条' +
      '<span class="audit-group-note">任务在网盘上找不到对应内容，可能每天空转</span></div>' +
      '<div class="lib-table lib-table-audit">' +
      '<div class="lib-row lib-head"><span>任务名</span><span>保存路径</span><span>结论</span></div>';
    missing.forEach(function(m){
      html += '<div class="lib-row">' +
        '<span class="lib-name" title="' + esc(m.taskname) + '">' + esc(m.taskname || '—') + '</span>' +
        '<span class="lib-sub" title="' + esc(m.savepath) + '">' + esc(m.savepath || '—') + '</span>' +
        '<span class="lib-sub" title="' + esc(m.reason) + '">' + esc(m.reason || '') + '</span>' +
        '</div>';
    });
    html += '</div></div>';
  }

  if(orphans.length){
    html += '<div class="audit-group">' +
      '<div class="audit-group-title">有内容但无任务指向 ' + orphans.length + ' 个目录' +
      '<span class="audit-group-note">网盘上有内容，但没有任何 QAS 任务指向它 —— 只提示，不会自动删</span></div>' +
      '<div class="audit-group-list">';
    orphans.forEach(function(o){
      html += '<span class="audit-chip" title="' + esc(o.dir) + '">' + esc(o.name || o.dir) + '</span>';
    });
    html += '</div></div>';
  }

  if(unverified.length){
    html += '<div class="audit-group">' +
      '<div class="audit-group-title">未核对 ' + unverified.length + ' 条' +
      '<span class="audit-group-note">保存路径不在本次扫描范围内，**不代表失效**（没扫到 ≠ 不存在）</span></div>' +
      '<div class="audit-group-list">';
    unverified.forEach(function(u){
      html += '<span class="audit-chip" title="' + esc(u.savepath) + '">' + esc(u.taskname || u.savepath) + '</span>';
    });
    html += '</div></div>';
  }
  box.innerHTML = html;
}
