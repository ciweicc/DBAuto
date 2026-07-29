// ============ Auth ============
var TOKEN = localStorage.getItem('auth_token')||'';
if(!TOKEN){location.href='/login.html'}
function authHeaders(){return{'X-Auth-Token':TOKEN}}
async function apiGet(url,retries=2,signal){
for(let i=0;i<=retries;i++){
try{
var r = await fetch(url,{headers:authHeaders(),signal:signal});
if(r.status===401){TOKEN='';localStorage.removeItem('auth_token');showToast('登录已过期，正在跳转...',false);setTimeout(function(){location.href='/login.html'},800);throw new Error('401')}
return r.json()
}catch(e){
if(e.name==='AbortError')throw e;
if(e.message==='401')throw e;
if(i<retries){await new Promise(r=>setTimeout(r,1000*(i+1)))}
else throw e
}
}
}
async function apiPost(url,body,retries=2){
for(let i=0;i<=retries;i++){
try{
var r = await fetch(url,{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify(body)});
if(r.status===401){TOKEN='';localStorage.removeItem('auth_token');showToast('登录已过期，正在跳转...',false);setTimeout(function(){location.href='/login.html'},800);throw new Error('401')}
return r.json()
}catch(e){
if(e.message==='401')throw e;
if(i<retries){await new Promise(r=>setTimeout(r,1000*(i+1)))}
else throw e
}
}
}

