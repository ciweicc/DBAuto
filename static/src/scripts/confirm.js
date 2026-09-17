// ============ Confirm Dialog ============
var _confirmResolve=null;
var _confirmReturnFocus=null;
function showConfirm(title,desc,confirmText,cancelText){
  return new Promise(function(resolve){
    if(_confirmResolve){ _confirmResolve(false); }
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
// 语义化的确认入口：**只有用户点了「确认」才返回 true**。
// 取消按钮 / 点击遮罩 / ESC 一律返回 false，且返回 false 时不执行任何副作用。
//
// 为什么单独提供它：showConfirm 返回的布尔值极易被误当成「Promise 一旦 resolve
// 就代表用户同意」，从而写出 `await showConfirm(...)` 后直接执行删除的代码
// （issue #27：点取消也照样删）。把「取返回值」固化在本函数内，
// 破坏性操作只调用 confirmThen，就不会再漏判。
function confirmThen(title,desc,confirmText,cancelText){
  return showConfirm(title,desc,confirmText,cancelText).then(function(ok){
    return ok === true;
  });
}

function resolveConfirm(result){
  var ov = document.getElementById('confirmOverlay');
  ov.classList.remove('show');
  ov.removeEventListener('keydown', _confirmTrap);
  if(_confirmReturnFocus && _confirmReturnFocus.focus) _confirmReturnFocus.focus();
  if(_confirmResolve){_confirmResolve(result);_confirmResolve=null}
}

