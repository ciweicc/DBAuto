// ============ Toast ============
function showToast(msg,ok,duration){
  var container=document.getElementById('toastContainer');
  var t=document.createElement('div');
  t.className='toast '+(ok?'ok':'err')+' show';
  var iconSpan = document.createElement('span');
  iconSpan.className = 'toast-icon';
  iconSpan.innerHTML = ok
    ?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-check-circle"/></svg>'
    :'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-x-circle"/></svg>';
  t.appendChild(iconSpan);
  var msgSpan=document.createElement('span');
  msgSpan.textContent=msg;
  t.appendChild(msgSpan);
  container.appendChild(t);
  var dur=duration||(ok?2500:4000);
  setTimeout(function(){
    t.classList.add('toast-out');
    setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t)},300);
  },dur);
}

