# Transfer Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve transfer efficiency, deduplication, progress visibility, conflict handling, and history completeness.

**Architecture:** Backend changes in `douban-app-v3.py` for transfer flow, deduplication, conflict protection, and history. Frontend changes in `static/app.js` and `static/index.html` for progress UI.

**Tech Stack:** Python stdlib (threading, concurrent.futures), vanilla JavaScript

---

## File Structure

| File | Responsibility |
|------|---------------|
| `douban-app-v3.py:614-680` | Transfer flow with parallel execution |
| `douban-app-v3.py:321-362` | History with fuzzy matching |
| `douban-app-v3.py:684-718` | Schedule conflict protection |
| `douban-app-v3.py:93-103` | Complete history recording |
| `static/app.js:407-412` | Progress counter update |
| `static/index.html:200-205` | Progress counter HTML |

---

### Task 1: Parallel Transfer Flow

**Covers:** Transfer efficiency improvement

**Files:**
- Modify: `douban-app-v3.py:614-680`

- [ ] **Step 1: Add parallel transfer with controlled concurrency**

Replace the serial transfer loop with ThreadPoolExecutor:

```python
def run_transfer(task_list, limit):
    with transfer_lock:
        transfer_status["running"] = True
        transfer_status["progress"].clear()
        transfer_status["summary"] = None
        transfer_status["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        transfer_status["stats"] = {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)}
    _sse_broadcast("status", {"running": True, "stats": transfer_status["stats"]})
    log(f"开始转存，上限 {limit}，共 {len(task_list)} 个")
    history = load_history()
    results = []
    
    def process_task(task):
        with transfer_lock:
            if not transfer_status["running"]:
                return None
        title, savepath = task["title"], task["savepath"]
        category = task.get("category", "movie")
        pattern = VIDEO_SUB
        replace = TV_REPLACE if category == "tv" else ""
        if title in history:
            log(f"已转存: {title}")
            with transfer_lock:
                transfer_status["stats"]["skipped"] += 1
            _sse_broadcast("status", {"running": True, "stats": dict(transfer_status["stats"])})
            return {"title": title, "status": "skipped", "msg": "已跳过"}
        log(f"搜索: {title}")
        with transfer_lock:
            transfer_status["stats"]["searched"] += 1
        _sse_broadcast("status", {"running": True, "stats": dict(transfer_status["stats"])})
        sr = search_pansou(title)
        if not sr:
            log(f"未找到: {title}")
            with transfer_lock:
                transfer_status["stats"]["failed"] += 1
            _sse_broadcast("status", {"running": True, "stats": dict(transfer_status["stats"])})
            return {"title": title, "status": "not_found", "msg": "未找到"}
        chosen = sr[0]
        log(f"找到: {chosen.get('note', title)}")
        res = add_and_run(title, chosen.get("url", ""), f"{savepath}/{title}", pattern, replace)
        log(f"  {res['msg']}")
        with transfer_lock:
            history[title] = {"date": datetime.now().strftime("%Y-%m-%d"), "status": res["status"]}
            if res["status"] in ("ok", "done"):
                transfer_status["stats"]["ok"] += 1
            else:
                transfer_status["stats"]["failed"] += 1
        _sse_broadcast("status", {"running": True, "stats": dict(transfer_status["stats"])})
        return {"title": title, "status": res["status"], "msg": res["msg"]}
    
    transferred = 0
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        for i, task in enumerate(task_list):
            with transfer_lock:
                if not transfer_status["running"]:
                    log("任务已终止")
                    break
                if transferred >= limit:
                    log(f"达到上限: {limit}")
                    break
            futures[executor.submit(process_task, task)] = task
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
                if result["status"] in ("ok", "done"):
                    transferred += 1
    
    save_history(history)
    summary = {"transferred": transferred, "total": len(task_list), "results": results}
    with transfer_lock:
        transfer_status["running"] = False
        transfer_status["summary"] = summary
    _sse_broadcast("status", {"running": False, "summary": summary, "stats": dict(transfer_status["stats"])})
    add_exec_record("transfer", f"转存 {transferred}/{len(task_list)}", "ok" if transferred > 0 else "empty")
    log(f"完成！转存 {transferred}")
```

- [ ] **Step 2: Deploy and verify**

Run: `pscp -pw xhbsa "E:\下载\ziyong\douban\douban-app-v3.py" root@192.168.1.1:/tmp/app.py && plink -pw xhbsa root@192.168.1.1 "docker cp /tmp/app.py douban-transfer-web:/app/app.py && docker restart douban-transfer-web"`

Expected: Container restarts, transfer now processes 3 items concurrently

---

### Task 2: Smart Deduplication

**Covers:** Fuzzy matching for history

**Files:**
- Modify: `douban-app-v3.py:321-362`
- Modify: `douban-app-v3.py:614-680` (transfer loop)

- [ ] **Step 1: Add fuzzy title normalization function**

Add after line 362 (after `cleanup_history`):

```python
import unicodedata

def _normalize_title(title):
    """标准化标题用于模糊匹配：去除标点、空格、特殊字符"""
    if not title:
        return ""
    # 转小写
    t = title.lower()
    # 去除Unicode标点和特殊字符
    t = ''.join(c for c in t if unicodedata.category(c) not in ('P', 'S', 'M', 'Z'))
    # 去除常见变体字符
    t = t.replace('：', '').replace('（', '').replace('）', '')
    t = t.replace('(', '').replace(')', '').replace('[', '').replace(']', '')
    t = t.replace('【', '').replace('】', '').replace('「', '').replace('」', '')
    return t

def _find_in_history(title, history):
    """在历史记录中查找标题，支持模糊匹配"""
    if title in history:
        return True
    norm = _normalize_title(title)
    if not norm:
        return False
    for h in history:
        if _normalize_title(h) == norm:
            return True
    return False
```

- [ ] **Step 2: Update transfer loop to use fuzzy matching**

In `run_transfer()`, replace:
```python
if title in history:
```

With:
```python
if _find_in_history(title, history):
```

- [ ] **Step 3: Deploy and verify**

Run: Deploy command as above

Expected: Transfers skip titles even with punctuation differences (e.g., "电影：名称" matches "电影名称")

---

### Task 3: Progress Visibility

**Covers:** UI progress counter

**Files:**
- Modify: `static/index.html:200-205`
- Modify: `static/app.js:62-74`
- Modify: `static/app.js:421-426`

- [ ] **Step 1: Add progress counter HTML in index.html**

Find the stats section in manual tab (around line 200):
```html
<div class="stats">
```

Add a new stat box at the beginning:
```html
<div class="stats">
<div class="stat" id="statProgress"><div class="sn2" id="sP">0/0</div><div class="sl">进度</div></div>
```

Do the same for schedule tab stats section (around line 274):
```html
<div class="stats" style="flex:0 0 auto">
<div class="stat" id="schedStatProgress"><div class="sn2" id="schedSP">0/0</div><div class="sl">进度</div></div>
```

- [ ] **Step 2: Update app.js SSE handler to include progress**

In the `connectSSE()` function, add progress update in the status event handler:

```javascript
eventSource.addEventListener('status',e=>{
  try{
    const d=JSON.parse(e.data);
    if(d.stats){
      DOM.sT.textContent=d.stats.searched||0;
      DOM.sO.textContent=d.stats.ok||0;
      DOM.sS.textContent=d.stats.skipped||0;
      DOM.sF.textContent=d.stats.failed||0;
      DOM.schedST.textContent=d.stats.searched||0;
      DOM.schedSO.textContent=d.stats.ok||0;
      DOM.schedSS.textContent=d.stats.skipped||0;
      DOM.schedSF.textContent=d.stats.failed||0;
      const total=d.stats.total||0;
      const done=(d.stats.ok||0)+(d.stats.skipped||0)+(d.stats.failed||0);
      document.getElementById('sP').textContent=done+'/'+total;
      document.getElementById('schedSP').textContent=done+'/'+total;
    }
    if(!d.running){
      if(d.summary)updateStats(d.summary);
      resetTransferBtn();
    }
  }catch(x){}
});
```

- [ ] **Step 3: Deploy and verify**

Run: Deploy static files

Expected: Stats section shows "0/0" progress counter that updates during transfer

---

### Task 4: Schedule Conflict Protection

**Covers:** Manual vs scheduled transfer conflict handling

**Files:**
- Modify: `douban-app-v3.py:1116-1153`

- [ ] **Step 1: Update transfer endpoint to handle conflicts gracefully**

Replace the transfer endpoint handler (around line 1116):

```python
elif route == "/api/transfer":
    with transfer_lock:
        is_running = transfer_status["running"]
    if is_running:
        # Check if it's a scheduled transfer running
        with schedule_lock:
            last_transfer = schedule_status.get("last_transfer")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "success": False, 
            "message": "定时转存正在进行中，请稍后再试",
            "conflict": True
        }).encode())
        return
    tasks = body.get("tasks", [])
    limit = body.get("limit", 5)
    if not tasks:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": False, "message": "no tasks"}).encode())
        return
    all_t = []
    for t in tasks:
        try:
            log(f"获取: {t['path']}/{t['type']}")
            items = get_douban_list(t["path"], t["type"], 20)
            log(f"获取到: {len(items)} 条")
            for i in items:
                all_t.append({"title": i["title"], "savepath": t["savepath"], "category": t.get("category", "movie")})
        except Exception as e:
            log(f"fetch error: {e}")
    seen, uniq = set(), []
    for t in all_t:
        if t["title"] not in seen:
            seen.add(t["title"])
            uniq.append(t)
    log(f"共获取 {len(uniq)}")
    Thread(target=run_transfer, args=(uniq, limit), daemon=True).start()
    self.send_response(200)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(json.dumps({"success": True, "message": f"开始处理 {len(uniq)}"}).encode())
```

- [ ] **Step 2: Update frontend to show conflict message**

In `static/app.js`, update the `go()` function:

```javascript
async function go(){
  const sel=gsManual();if(!sel.length){alert('请至少选择一个榜单');return}
  const lim=parseInt(document.getElementById('lim').value)||5;
  DOM.btn.disabled=true;DOM.btn.innerHTML='<span class="sp"></span>执行中...';
  DOM.right.style.display='block';
  DOM.stopBtn.style.display='inline-block';
  logLines=[];renderLog();
  try{
    const r=await authFetch('/api/transfer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tasks:sel,limit:lim})});
    const d=await r.json();
    if(d.success){
      poll();
    } else {
      pushLogLine(d.message);
      resetTransferBtn();
      if(d.conflict){
        showToast('定时转存进行中，请稍后',false);
      }
    }
  }
  catch(e){pushLogLine(e.message);resetTransferBtn()}
}
```

- [ ] **Step 3: Deploy and verify**

Run: Deploy both Python and JS files

Expected: Manual transfer shows clear message when scheduled transfer is running

---

### Task 5: Complete History Recording

**Covers:** Record all transfer attempts

**Files:**
- Modify: `douban-app-v3.py:614-680` (transfer loop)

- [ ] **Step 1: Record failed attempts in history**

In `run_transfer()`, after handling "未找到" case, add history recording:

```python
sr = search_pansou(title)
if not sr:
    log(f"未找到: {title}")
    with transfer_lock:
        transfer_status["stats"]["failed"] += 1
        history[title] = {"date": datetime.now().strftime("%Y-%m-%d"), "status": "not_found"}
    _sse_broadcast("status", {"running": True, "stats": dict(transfer_status["stats"])})
    return {"title": title, "status": "not_found", "msg": "未找到"}
```

And after add_and_run fails:

```python
res = add_and_run(title, chosen.get("url", ""), f"{savepath}/{title}", pattern, replace)
log(f"  {res['msg']}")
with transfer_lock:
    history[title] = {"date": datetime.now().strftime("%Y-%m-%d"), "status": res["status"], "msg": res.get("msg", "")}
    if res["status"] in ("ok", "done"):
        transfer_status["stats"]["ok"] += 1
    else:
        transfer_status["stats"]["failed"] += 1
```

- [ ] **Step 2: Deploy and verify**

Run: Deploy Python file

Expected: History now contains failed attempts with status and message

---

### Task 6: Update transfer_status Stats Initialization

**Covers:** Ensure total count is always available

**Files:**
- Modify: `douban-app-v3.py:42`

- [ ] **Step 1: Add total field to stats initialization**

Update line 42:

```python
transfer_status = {"running": False, "progress": deque(maxlen=500), "summary": None, "start_time": None, "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0}}
```

Also update in `run_transfer()`:

```python
transfer_status["stats"] = {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)}
```

- [ ] **Step 2: Deploy and verify**

Run: Deploy Python file

Expected: Stats always include total count for progress calculation

---

## Execution

This plan has 6 tightly coupled tasks. Execute **inline** in this session.

After all tasks are complete:
1. Deploy all files to router
2. Restart container
3. Test each improvement manually
