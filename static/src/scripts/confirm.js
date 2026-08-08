// ============ Confirm Dialog ============
var _confirmResolve=null;
var _confirmReturnFocus=null;
function showConfirm(title,desc,confirmText,cancelText){
  return new Promise(function(resolve){
    _confirmResolve=resolve;
    _confirmReturnFocus = document.activeElement;
    document.getElementById('confirmTitle').textContent=title||'确认';
    document.getElementById('confirmDesc').textContent=desc||'';
    document.getElementById('confirmOk').textContent=confirmText||'确认';
    document.getElementById('confirmCancel').textContent=cancelText||'取消';
    var ov = document.getElementById('confirmOverlay');
    ov.classList.add('show');
    var okBtn = document.getElementById('confirmOk');
    if(okBtn) okBtn.focus();
    ov.addEventListener('keydown', _confirmTrap);
  });
}
function _confirmTrap(e){
  if(e.key !== 'Tab') return;
  var ov = document.getElementById('confirmOverlay');
  var f = ov.querySelectorAll('button:not([disabled])');
  if(!f.length) return;
  var first = f[0], last = f[f.length-1];
  if(e.shiftKey && document.activeElement === first){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey && document.activeElement === last){ e.preventDefault(); first.focus(); }
}
function resolveConfirm(result){
  var ov = document.getElementById('confirmOverlay');
  ov.classList.remove('show');
  ov.removeEventListener('keydown', _confirmTrap);
  if(_confirmReturnFocus && _confirmReturnFocus.focus) _confirmReturnFocus.focus();
  if(_confirmResolve){_confirmResolve(result);_confirmResolve=null}
}

