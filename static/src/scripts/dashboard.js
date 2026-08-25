// ============ Dashboard 共享数据源 ============
// 设置页「运行概览」模块已移除（与概览页 KPI 信息重复，见 docs/UI_Roadmap_Next.md 7.1）。
// 本文件仅保留共享数据源与公共工具：
//   - fetchDashboardAll：概览页（overview.js）复用的 /api/dashboard/all 缓存封装；
//   - loadDashboard：顶栏版本号更新，保留函数名以兼容 init.js / SSE 调用点，
//     拉取失败静默（概览页有自己的加载态与错误呈现）。

// 共享概览数据源（OPT-03）：底层走 cachedApiGet 做 TTL 缓存，并发请求复用同一 Promise 去重。
async function fetchDashboardAll(force){
  return cachedApiGet('/api/dashboard/all', 8000, force);
}

/**
 * 加载概览数据（入口）。保持函数名 loadDashboard 以兼容 init.js / SSE 调用。
 * @param {boolean} [force] true 时忽略缓存强制重新拉取。
 */
async function loadDashboard(force){
  try{
    var d = await fetchDashboardAll(force);
    if(d && d.version){
      var el = document.getElementById('headerVersion');
      if(el) el.textContent = 'v' + d.version;
    }
  }catch(e){ /* 静默失败 */ }
}

/**
 * HTML 转义，防止调度时间等字符串注入（globals/history/overview/tmdb 共用）。
 */
function esc(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
