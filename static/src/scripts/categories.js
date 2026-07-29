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

function parseCategories(){
  for(var gk in C){
    var g = C[gk];
    var container = null;
    if(gk==='movie')container=document.getElementById('catsMovie');
    else if(gk==='tv')container=document.getElementById('catsTV');
    else container=document.getElementById('catsVariety');
    if(!container)continue;
    container.textContent='';
    for(var sn in g.subs){
      var s = g.subs[sn];
      var subTitle = document.createElement('div');
      subTitle.className = 'sub-title';
      subTitle.textContent = sn + ' ';
      var selAll = document.createElement('span');
      selAll.className = 'sel-all';
      selAll.textContent = '全选';
      selAll.addEventListener('click', function() { toggleSub(this); });
      subTitle.appendChild(selAll);
      container.appendChild(subTitle);
      var chipsDiv = document.createElement('div');
      chipsDiv.className = 'chips';
      for(var ti=0; ti<s.types.length; ti++){
        var t = s.types[ti];
        var id = gk+'_'+sn.replace(/\s/g,'_')+'_'+t.replace(/\s/g,'_');
        chipsDiv.appendChild(_makeChip(id, s.path, t, gk, g.name, t, toggleChip));
      }
      container.appendChild(chipsDiv);
    }
  }
}

function parseSchedCats(){
  for(var gk in C){
    var g = C[gk];
    var container = null;
    if(gk==='movie')container=document.getElementById('schedCatsMovie');
    else if(gk==='tv')container=document.getElementById('schedCatsTV');
    else container=document.getElementById('schedCatsVariety');
    if(!container)continue;
    container.textContent='';
    for(var sn in g.subs){
      var s = g.subs[sn];
      var subTitle = document.createElement('div');
      subTitle.className = 'sub-title';
      subTitle.textContent = sn + ' ';
      var selAll = document.createElement('span');
      selAll.className = 'sel-all';
      selAll.textContent = '全选';
      selAll.addEventListener('click', function() { toggleSubSched(this); });
      subTitle.appendChild(selAll);
      container.appendChild(subTitle);
      var chipsDiv = document.createElement('div');
      chipsDiv.className = 'chips';
      for(var ti=0; ti<s.types.length; ti++){
        var t = s.types[ti];
        var id = 'sch_'+gk+'_'+sn.replace(/\s/g,'_')+'_'+t.replace(/\s/g,'_');
        chipsDiv.appendChild(_makeChip(id, s.path, t, gk, g.name, t, toggleSchedChip));
      }
      container.appendChild(chipsDiv);
    }
  }
}

function toggleChip(el){el.classList.toggle('on')}
function toggleSchedChip(el){el.classList.toggle('on');autoSaveSchedule()}
function toggleWishCat(el){el.classList.toggle('on')}
function toggleWishEnabled(el){el.classList.toggle('on')}
function renderWishAccounts(accounts){
var el=document.getElementById('wishAccountsList');
el.innerHTML='';
if(!accounts||!accounts.length)return;
for(var i=0;i<accounts.length;i++){el.appendChild(_makeWishAccRow(i,accounts[i]||{}))}
}
function _makeWishAccRow(i,a){
var row=document.createElement('div');
row.className='wish-acc-row';
row.style.cssText='border:1px solid var(--border);border-radius:12px;padding:12px;background:rgba(255,255,255,.02)';
var headerDiv = document.createElement('div');
headerDiv.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px';
var headerSpan = document.createElement('span');
headerSpan.style.cssText = 'font-size:13px;font-weight:600;color:var(--text2);font-family:var(--font-display)';
headerSpan.textContent = '账号 '+(i+1);
var removeBtn = document.createElement('button');
removeBtn.className = 'btn btn-sm btn-outline';
removeBtn.style.color = 'var(--text3)';
removeBtn.textContent = '移除';
removeBtn.addEventListener('click', function(){ removeWishAccount(this); });
headerDiv.appendChild(headerSpan);
headerDiv.appendChild(removeBtn);

function _makeFormGroup(labelText, inputClass, placeholder, value, type) {
  var group = document.createElement('div');
  group.className = 'form-group';
  group.style.marginBottom = '8px';
  var label = document.createElement('label');
  label.textContent = labelText;
  var input = document.createElement('input');
  input.type = type || 'text';
  input.className = inputClass;
  input.placeholder = placeholder;
  if(value) input.value = value;
  group.appendChild(label);
  group.appendChild(input);
  return group;
}

row.appendChild(headerDiv);
row.appendChild(_makeFormGroup('备注','wish-acc-name','可选备注名',a.name||''));
row.appendChild(_makeFormGroup('豆瓣用户 ID','wish-acc-uid','豆瓣个人主页 URL 中的 ID',a.uid||''));
var cookieGroup = _makeFormGroup('豆瓣 Cookie','wish-acc-cookie','留空不修改','','password');
cookieGroup.style.marginBottom = '0';
row.appendChild(cookieGroup);
return row;
}
function addWishAccount(){
var el=document.getElementById('wishAccountsList');
el.appendChild(_makeWishAccRow(el.children.length,{}));
}
function removeWishAccount(btn){btn.closest('.wish-acc-row').remove()}
function collectWishAccounts(){
var accounts=[];
document.querySelectorAll('#wishAccountsList .wish-acc-row').forEach(function(row){
var name=row.querySelector('.wish-acc-name').value.trim();
var uid=row.querySelector('.wish-acc-uid').value.trim();
var cookie=row.querySelector('.wish-acc-cookie').value;
if(uid)accounts.push({uid:uid,cookie:cookie||'***',name:name});
});
return accounts;
}

function toggleSub(btn){
  var chips = btn.parentElement.nextElementSibling.querySelectorAll('.chip');
  var all = true;
  for(var i=0;i<chips.length;i++){if(!chips[i].classList.contains('on')){all=false;break}}
  for(var i=0;i<chips.length;i++){if(all)chips[i].classList.remove('on');else chips[i].classList.add('on')}
}
function toggleSubSched(btn){
  var chips = btn.parentElement.nextElementSibling.querySelectorAll('.chip');
  var all = true;
  for(var i=0;i<chips.length;i++){if(!chips[i].classList.contains('on')){all=false;break}}
  for(var i=0;i<chips.length;i++){if(all)chips[i].classList.remove('on');else chips[i].classList.add('on')}
  autoSaveSchedule();
}

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

function syncToSchedule(){
  document.querySelectorAll('#tabSchedule .chip').forEach(function(c){c.classList.remove('on')});
  var manual = document.querySelectorAll('#tabManual .chip.on');
  var paths = new Set();
  manual.forEach(function(m){
    var key = m.dataset.path+'/'+m.dataset.type;
    paths.add(key);
  });
  document.querySelectorAll('#tabSchedule .chip').forEach(function(c){
    var key = c.dataset.path+'/'+c.dataset.type;
    if(paths.has(key))c.classList.add('on');
  });
  autoSaveSchedule();
  showToast('已同步到定时任务',true);
}

