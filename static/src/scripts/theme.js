// ============ Theme ============
// 三态：dark / light / auto（跟随系统 prefers-color-scheme）
var themeMedia = null;

function systemPrefersDark(){
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// 根据存储的模式（含 auto）应用实际主题，并同步按钮图标/文案
function applyTheme(mode){
  var actual = mode === 'auto' ? (systemPrefersDark() ? 'dark' : 'light') : mode;
  document.documentElement.setAttribute('data-theme', actual);
  var btn = document.getElementById('themeBtn');
  if(!btn) return;
  var iconId = mode === 'dark' ? 'icon-moon' : mode === 'light' ? 'icon-sun' : 'icon-contrast';
  var label  = mode === 'dark' ? '深色' : mode === 'light' ? '浅色' : '跟随系统';
  btn.textContent = '';
  var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '18'); svg.setAttribute('height', '18');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#' + iconId);
  svg.appendChild(use); btn.appendChild(svg);
  var tip = '主题：' + label + '（点击切换）';
  btn.title = tip;
  btn.setAttribute('aria-label', tip);
  btn.dataset.theme = mode;
}

function initTheme(){
  var t = localStorage.getItem('theme') || 'dark';
  if(['dark','light','auto'].indexOf(t) === -1) t = 'dark';
  applyTheme(t);
  // 跟随系统：仅当模式为 auto 时监听系统主题变化
  if(window.matchMedia){
    themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    var handler = function(){ if((localStorage.getItem('theme') || 'dark') === 'auto') applyTheme('auto'); };
    if(themeMedia.addEventListener) themeMedia.addEventListener('change', handler);
    else if(themeMedia.addListener) themeMedia.addListener(handler);
  }
}

function toggleTheme(){
  var cur = localStorage.getItem('theme') || 'dark';
  var next = cur === 'dark' ? 'light' : cur === 'light' ? 'auto' : 'dark';
  localStorage.setItem('theme', next);
  applyTheme(next);
  var label = next === 'dark' ? '深色' : next === 'light' ? '浅色' : '跟随系统';
  srAnnounce('主题已切换为' + label);
}
