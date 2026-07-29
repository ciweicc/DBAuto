// ============ Theme ============
function initTheme(){
  var t = localStorage.getItem('theme')||'dark';
  document.documentElement.setAttribute('data-theme',t);
  var icon = t==='dark'?'icon-moon':'icon-sun';
  var themeBtnEl = document.getElementById('themeBtn');
  themeBtnEl.textContent='';
  var themeSvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  themeSvg.setAttribute('width','18');
  themeSvg.setAttribute('height','18');
  themeSvg.setAttribute('viewBox','0 0 24 24');
  themeSvg.setAttribute('fill','none');
  themeSvg.setAttribute('stroke','currentColor');
  themeSvg.setAttribute('stroke-width','2');
  var themeUse = document.createElementNS('http://www.w3.org/2000/svg','use');
  themeUse.setAttribute('href','#'+icon);
  themeSvg.appendChild(themeUse);
  themeBtnEl.appendChild(themeSvg);
}
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur==='dark'?'light':'dark';
  document.documentElement.setAttribute('data-theme',next);
  localStorage.setItem('theme',next);
  var icon = next==='dark'?'icon-moon':'icon-sun';
  var themeBtnEl = document.getElementById('themeBtn');
  themeBtnEl.textContent='';
  var themeSvg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  themeSvg.setAttribute('width','18');
  themeSvg.setAttribute('height','18');
  themeSvg.setAttribute('viewBox','0 0 24 24');
  themeSvg.setAttribute('fill','none');
  themeSvg.setAttribute('stroke','currentColor');
  themeSvg.setAttribute('stroke-width','2');
  var themeUse = document.createElementNS('http://www.w3.org/2000/svg','use');
  themeUse.setAttribute('href','#'+icon);
  themeSvg.appendChild(themeUse);
  themeBtnEl.appendChild(themeSvg);
}

