// ============ Animation Utils ============
function easeOutCubic(t){return 1-Math.pow(1-t,3)}
function animateNumber(el, target, duration){
  duration = duration||800;
  var start = 0;
  try{start = parseInt(el.textContent)||0}catch(e){start=0}
  if(start===target)return;
  var startTime = null;
  function step(ts){
    if(!startTime)startTime=ts;
    var p = Math.min((ts-startTime)/duration, 1);
    var eased = easeOutCubic(p);
    var val = Math.round(start + (target-start)*eased);
    el.textContent = val;
    if(p<1)requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

