// ============ Sound (操作音效) ============
// 使用 Web Audio API 合成短音效，无需外部音频资源
// 音效默认开启，可通过 localStorage 'soundEnabled' 关闭
var SoundEnabled = localStorage.getItem('soundEnabled');
SoundEnabled = (SoundEnabled === null) ? true : (SoundEnabled === 'true');
var _audioCtx = null;

function _getAudioCtx(){
  if(!_audioCtx){
    try{ _audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch(e){ return null; }
  }
  // 浏览器自动播放策略：用户手势后需恢复 AudioContext
  if(_audioCtx && _audioCtx.state === 'suspended'){ _audioCtx.resume().catch(function(){}); }
  return _audioCtx;
}

function _beep(freq, duration, type, gainVal, when){
  var ctx = _getAudioCtx();
  if(!ctx) return;
  var t0 = ctx.currentTime + (when || 0);
  var osc = ctx.createOscillator();
  var g = ctx.createGain();
  osc.type = type || 'sine';
  osc.frequency.setValueAtTime(freq, t0);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.linearRampToValueAtTime(gainVal || 0.18, t0 + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  osc.connect(g); g.connect(ctx.destination);
  osc.start(t0); osc.stop(t0 + duration + 0.02);
}

// name: 'success' | 'error' | 'click'
function playSound(name){
  if(!SoundEnabled) return;
  try{
    if(name === 'success'){
      _beep(660, 0.12, 'sine', 0.18, 0);
      _beep(880, 0.16, 'sine', 0.16, 0.10);
    } else if(name === 'error'){
      _beep(220, 0.20, 'sawtooth', 0.12, 0);
      _beep(160, 0.26, 'sawtooth', 0.10, 0.07);
    } else if(name === 'click'){
      _beep(440, 0.05, 'triangle', 0.10, 0);
    }
  }catch(e){}
}

function setSoundEnabled(v){
  SoundEnabled = !!v;
  localStorage.setItem('soundEnabled', SoundEnabled ? 'true' : 'false');
  updateSoundBtn();
}

function toggleSound(){
  setSoundEnabled(!SoundEnabled);
  showToast(SoundEnabled ? '已开启操作音效' : '已关闭操作音效', true);
}

var _SOUND_ICON_ON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
var _SOUND_ICON_OFF = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4z"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';

function updateSoundBtn(){
  var btn = document.getElementById('soundBtn');
  if(!btn) return;
  btn.innerHTML = SoundEnabled ? _SOUND_ICON_ON : _SOUND_ICON_OFF;
  btn.title = SoundEnabled ? '操作音效：开' : '操作音效：关';
  btn.style.opacity = SoundEnabled ? '1' : '0.55';
}
