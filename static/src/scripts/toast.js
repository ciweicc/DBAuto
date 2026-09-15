// ============ Toast ============
// opts.ac: {label, onClick} —— 可选的行动按钮（如「撤销」）。
// 带行动按钮时容器可点击，且默认展示时长延长到 5s，给用户留出反应时间。
function showToast(msg,ok,duration,opts){
  var container=document.getElementById('toastContainer');
  var t=document.createElement('div');
  t.className='toast '+(ok?'ok':'err')+' show';
  // 4.5 aria-live 分级：错误为打断式播报(alert)，成功提示维持容器上的 polite
  if(!ok)t.setAttribute('role','alert');
  var iconSpan = document.createElement('span');
  iconSpan.className = 'toast-icon';
  iconSpan.innerHTML = ok
    ?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-check-circle"/></svg>'
    :'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-x-circle"/></svg>';
  t.appendChild(iconSpan);
  var msgSpan=document.createElement('span');
  msgSpan.textContent=msg;
  t.appendChild(msgSpan);
  var ac = opts && opts.ac;
  if(ac && ac.label && typeof ac.onClick === 'function'){
    var actionBtn=document.createElement('button');
    actionBtn.type='button';
    actionBtn.className='toast-action';
    actionBtn.textContent=ac.label;
    actionBtn.addEventListener('click', function(e){
      e.stopPropagation();
      _dismissToast(t);
      ac.onClick();
    });
    t.appendChild(actionBtn);
    t.classList.add('has-action');
  }
  container.appendChild(t);
  var dur=duration||(ac?5000:(ok?2500:4000));
  setTimeout(function(){_dismissToast(t)},dur);
}
// 幂等移除：动画结束后再摘除节点，重复调用安全
function _dismissToast(t){
  if(!t||t._dismissed) return;
  t._dismissed=true;
  t.classList.add('toast-out');
  setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t)},300);
}

