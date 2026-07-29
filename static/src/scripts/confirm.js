// ============ Confirm Dialog ============
var _confirmResolve=null;
function showConfirm(title,desc,confirmText,cancelText){
  return new Promise(function(resolve){
    _confirmResolve=resolve;
    document.getElementById('confirmTitle').textContent=title||'确认';
    document.getElementById('confirmDesc').textContent=desc||'';
    document.getElementById('confirmOk').textContent=confirmText||'确认';
    document.getElementById('confirmCancel').textContent=cancelText||'取消';
    document.getElementById('confirmOverlay').classList.add('show');
  });
}
function resolveConfirm(result){
  document.getElementById('confirmOverlay').classList.remove('show');
  if(_confirmResolve){_confirmResolve(result);_confirmResolve=null}
}

