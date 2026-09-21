// ============ 侧边栏选中动效（issue #84） ============
// 用户诉求原文只有一句「侧边栏选项添加选中动态效果」。
//
// 问题现场：旧版选中态只有一条静态的 `.active` 底色，切换页签时旧项与新项
// 在**同一帧**里换掉——看得见「现在是哪个」，看不见「从哪个挪到哪个」。
// 侧边栏是应用的主导航，一天要点几十次，每次都是硬切，观感就是「卡」。
//
// 做法：把「选中」具象成一块**唯一**的滑动底板（.side-nav-ind），由 JS 定位到
// 当前选中项，切换时用 transform 平移过去；再给选中项图标一个一次性脉冲作为落点。
//
// 三个容易做错、这里显式钉住的点：
//
// 1) **不与既有的 .active 样式打架**
//    底板本身就是那块选中底色。若选中项自己再画一层，两层叠加会明显过亮，
//    而且旧项底色瞬灭 —— 看着就像动效没生效。因此底板就位后给 ul 加 `.ind-on`，
//    由 CSS 把 `.active` 的底色/内阴影让出来（见 main.css）。
//
// 2) **首帧不能滑**
//    页面初次渲染时选中项在中间（默认概览），若开着过渡，底板会从顶部滑下来，
//    像动画故障。所以初始的 transition 是 none，先定位、再补一帧打开过渡。
//
// 3) **窄屏抽屉里不做无意义动画**
//    抽屉是 translateX 滑入的，用户看不到定位过程；且抽屉从 display 变化到
//    可见的过程中量到的几何可能是 0。这里对抽屉态强制「直接定位 + 不动画」。
var SIDENAV_MIN_H = 24; // 量不到高度时的兜底底板高（正常来自 JS 写回的 --side-nav-h）

// 把底板挪到当前选中项：只改 transform / height，不碰 left/top，避免每帧重排。
function _sideNavMoveInd(nav, item, animate){
  var ind = nav && nav.querySelector('.side-nav-ind');
  if(!ind || !item) return;
  var top = item.offsetTop;
  var h = item.offsetHeight;
  if(!(h > 0)) return; // 抽屉尚未参与布局时量不到，等下一次同步
  // 首帧（还没摆过位置）必须先「定位 + 打开过渡」，且这一步本身不能是动画：
  // 否则底板会从顶部 0 滑到当前位置，观感像动画故障。
  // 之后的每一次同步才是「打开过渡 + 平移」。
  var first = !ind.classList.contains('ready');
  if(animate && !first){
    ind.classList.add('anim');
  }else{
    ind.classList.remove('anim');
  }
  ind.style.height = h + 'px';
  ind.style.transform = 'translateY(' + top + 'px)';
  ind.classList.add('ready');
  // 选中项自己的底色/内阴影交给底板（见上方注释 1）
  nav.classList.add('ind-on');
  // 供媒体查询外的几何断言使用（走查脚本读它，避免依赖 getComputedStyle 的字符串）
  ind.setAttribute('data-nav-top', String(top));
  ind.setAttribute('data-nav-h', String(h));
  // 首帧摆好位置后、下一帧再打开过渡：此刻 transform 已经等于目标值，
  // 打开过渡不会产生位移，但后续切换就是平滑的。
  if(first && animate){
    requestAnimationFrame(function(){
      if(ind.classList.contains('ready')) ind.classList.add('anim');
    });
  }
}

function _sideNavSync(widthChanged){
  var nav = document.querySelector('.side-nav');
  if(!nav) return;
  var ind = nav.querySelector('.side-nav-ind');
  if(!ind) return;
  var active = nav.querySelector('.side-nav-item.active');
  if(active){
    var narrow = (typeof isNarrowViewport === 'function') ? isNarrowViewport() : false;
    var sidebar = document.querySelector('.sidebar');
    var wantAnim = !narrow || !(sidebar && sidebar.classList.contains('open'));
    _sideNavMoveInd(nav, active, wantAnim);
  }
  _sideNavPingActive(nav);
}

// 选中项图标脉冲：一次性反馈，动画结束即摘掉 class（不做常驻呼吸动画 —— 那会让整排都在动）
function _sideNavPingActive(nav){
  var el = (nav || document.querySelector('.side-nav') || {}).querySelector
    ? (nav || document.querySelector('.side-nav')).querySelector('.side-nav-item.active') : null;
  if(!el) return;
  el.classList.remove('side-btn-pulse');
  // 强制回流，保证连续切换同一项时动画能重播
  void el.offsetWidth;
  el.classList.add('side-btn-pulse');
  setTimeout(function(){ el.classList.remove('side-btn-pulse'); }, 460);
}

// 布局重算（窗口缩放 / 抽屉开合 / 密度切换）：只重新定位，不做动画 ——
// 这不是用户操作，滑一段只会显得界面在抖。
function _sideNavSyncNoAnim(){
  var nav = document.querySelector('.side-nav');
  if(!nav) return;
  var ind = nav.querySelector('.side-nav-ind');
  if(!ind) return;
  var active = nav.querySelector('.side-nav-item.active');
  if(!active) return;
  var top = active.offsetTop, h = active.offsetHeight;
  if(!(h > 0)) return;
  var keepsAnim = ind.classList.contains('anim');
  ind.classList.remove('anim');
  ind.style.height = h + 'px';
  ind.style.transform = 'translateY(' + top + 'px)';
  ind.classList.add('ready');
  nav.classList.add('ind-on');
  ind.setAttribute('data-nav-top', String(top));
  ind.setAttribute('data-nav-h', String(h));
  if(keepsAnim){
    // 同步重排后立刻把过渡还回来，否则下一次切换又变成硬切
    void ind.offsetWidth;
    ind.classList.add('anim');
  }
}

// 侧边栏宽度变化（窗口缩放、密度切换）或可见性变化时重新定位。
// 指数退避重试：抽屉从 display 变化到参与布局之间量到的几何是 0，
// 一次 setTimeout(0) 不够稳，重试几次直到量到非零高度为止。
function _sideNavRefresh(){
  _sideNavSyncNoAnim();
  var delay = 40;
  var tries = 0;
  (function again(){
    var ind = document.querySelector('.side-nav .side-nav-ind');
    var active = document.querySelector('.side-nav-item.active');
    if(active && active.offsetHeight > 0 && ind && ind.offsetHeight > 0) return;
    if(tries++ >= 5) return;
    setTimeout(function(){ _sideNavSyncNoAnim(); again(); }, delay);
    delay *= 2;
  })();
}

// 抽屉打开 / 关闭后同步（init.js 的 toggleSidebar 会调用）
function sideNavDrawerSync(open){
  var sidebar = document.querySelector('.sidebar');
  if(sidebar) sidebar.classList.toggle('drawer-no-anim', !!open);
  _sideNavRefresh();
}
