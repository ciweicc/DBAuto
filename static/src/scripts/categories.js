// ============ Categories ============
function _makeChip(id, path, type, group, gname, label, onClick) {
  var chip = document.createElement('span');
  chip.className = 'chip';
  chip.id = 'c_' + id;
  chip.dataset.id = id;
  chip.dataset.path = path;
  chip.dataset.type = type;
  chip.dataset.group = group;
  chip.dataset.gname = gname;
  chip.textContent = label;
  chip.addEventListener('click', function() { onClick(chip); });
  return chip;
}

function parseCats(containers, onClick, idPrefix, selAllSave){
  // 参数化渲染分类（OPT-34）：containers 指定三组容器 id，onClick 为 chip 点击回调，idPrefix 区分手动/定时
  // selAllSave：true 时「全选」会调用 autoSaveSchedule()（定时页），false 时不保存（手动页）。默认 false。
  selAllSave = selAllSave || false;
  for(var gk in C){
    var g = C[gk];
    var containerId = containers[gk] || containers.variety;
    var container = document.getElementById(containerId);
    if(!container) continue;
    container.textContent = '';
    for(var sn in g.subs){
      var s = g.subs[sn];
      var subTitle = document.createElement('div');
      subTitle.className = 'sub-title';
      subTitle.textContent = sn + ' ';
      var selAll = document.createElement('span');
      selAll.className = 'sel-all';
      selAll.textContent = '全选';
      selAll.addEventListener('click', function() { toggleSubChips(this, selAllSave); });
      subTitle.appendChild(selAll);
      container.appendChild(subTitle);
      var chipsDiv = document.createElement('div');
      chipsDiv.className = 'chips';
      for(var ti=0; ti<s.types.length; ti++){
        var t = s.types[ti];
        var id = idPrefix + gk + '_' + sn.replace(/\s/g,'_') + '_' + t.replace(/\s/g,'_');
        chipsDiv.appendChild(_makeChip(id, s.path, t, gk, g.name, t, onClick));
      }
      container.appendChild(chipsDiv);
    }
  }
}

function parseCategories(){
  // issue #68 追问：合并「转存」「定时」两页后，分类 chips **只有这一份**，
  // 既是「立即转存」的输入，也是「定时转存」的输入。
  // 因此点击即 autoSaveSchedule()：改一下勾选就等于同时改了两处，不再需要「同步到定时任务」。
  parseCats({movie:'catsMovie', tv:'catsTV', variety:'catsVariety'}, toggleChip, '', true);
}

function toggleChip(el){el.classList.toggle('on');autoSaveSchedule()}
// 豆瓣想看同步相关函数（toggleWishCat/toggleWishEnabled/renderWishAccounts 等）已随功能移除

function toggleSubChips(btn, save){
  // 参数化「全选/取消全选」某子分类下所有 chip（OPT-34 合并原 toggleSub/toggleSubSched）
  var chips = btn.parentElement.nextElementSibling.querySelectorAll('.chip');
  var all = true;
  for(var i=0;i<chips.length;i++){if(!chips[i].classList.contains('on')){all=false;break}}
  for(var i=0;i<chips.length;i++){if(all)chips[i].classList.remove('on');else chips[i].classList.add('on')}
  if(save) autoSaveSchedule();
}
function toggleSub(btn){ toggleSubChips(btn, false); }
function toggleSubSched(btn){ toggleSubChips(btn, true); }

function getSelectedTasks(prefix){
  var tasks=[], seen=new Set();
  var catBase = (APP_PATHS.category_base||'/影视').replace(/\/+$/,'')||'/影视';
  document.querySelectorAll('#'+prefix+' .chip.on').forEach(function(el){
    var k = el.dataset.path+'/'+el.dataset.type;
    if(seen.has(k))return; seen.add(k);
    tasks.push({path:el.dataset.path, type:el.dataset.type, savepath:catBase+'/'+el.dataset.gname, category:el.dataset.group});
  });
  return tasks;
}


