# verify.py — 链接有效性检测 + 失效自动修复
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import get_ident
from config import load_settings
from utils import log
from dedup import _get_qas_client, qas_breaker
from search import search_pansou
from resilience import CircuitBreakerOpen

EXPIRED_CHECK_CONCURRENCY = 5


def validate_share_link(url):
    """验证分享链接是否有效"""
    try:
        client = _get_qas_client()
        r = client.get_share_detail(url)
        return r.get("success", False), r.get("message", "")
    except Exception as e:
        return False, str(e)


def _check_single_expired(task):
    url = task.get("shareurl", "")
    try:
        client = _get_qas_client()
        result = client.get_share_detail(url)
        if not result.get("success"):
            return task, True
        return task, False
    except Exception as e:
        log("检测分享链接失败 {}: {}".format(url, e))
        return task, True


def check_expired_tasks(transfer_status=None, limit=None):
    """检测失效链接

    Args:
        transfer_status: 全局状态字典（用于检查 stop 标志）
        limit: 最多检测多少条
    Returns:
        失效任务列表
    """
    try:
        client = _get_qas_client()
        data = qas_breaker.call(client.get_data).get("data", {})
        tasks = data.get("tasklist", [])
        settings = load_settings()
        expired_dirs = settings.get("expired_check", {}).get("directories", [])
        to_check = [t for t in tasks if t.get("shareurl", "") and "quark.cn" in t.get("shareurl", "")]
        if expired_dirs:
            to_check = [t for t in to_check if t.get("savepath", "") and any(d in t.get("savepath", "") for d in expired_dirs)]
            log("失效检测目录范围: {}".format(expired_dirs))
        if limit:
            to_check = to_check[:limit]
        if not to_check:
            log("失效检测: 无符合条件的任务")
            return []
        log("检测失效链接: {} 个，并发数: {}".format(len(to_check), EXPIRED_CHECK_CONCURRENCY))
        expired = []
        with ThreadPoolExecutor(max_workers=EXPIRED_CHECK_CONCURRENCY) as executor:
            future_map = {executor.submit(_check_single_expired, t): t for t in to_check}
            for future in as_completed(future_map):
                if transfer_status and transfer_status.get("stop"):
                    for f in future_map:
                        f.cancel()
                    log("检测已被用户终止")
                    break
                try:
                    task, is_expired = future.result()
                    if is_expired:
                        expired.append(task)
                except Exception as e:
                    task = future_map[future]
                    log("检测任务异常 {}: {}".format(task.get("shareurl", ""), e))
                    expired.append(task)
        log("检测完成: {} 个失效".format(len(expired)))
        return expired
    except CircuitBreakerOpen as e:
        log("QAS 熔断: {}".format(e))
        return []
    except Exception as e:
        log("检测失效出错: {}".format(e))
        return []


def update_expired_task(task, new_url):
    """更新失效任务的链接"""
    try:
        client = _get_qas_client()
        data = client.get_data().get("data", {})
        tasks = data.get("tasklist", [])
        old_url = task.get("shareurl", "")
        updated = False
        for t in tasks:
            if t.get("shareurl") == old_url:
                t["shareurl"] = new_url
                updated = True
                break
        if updated:
            data["tasklist"] = tasks
            result = client.update(data)
            return result.get("success", False)
        return False
    except Exception as e:
        log("更新失效出错: {}".format(e))
        return False


def fix_expired_tasks(transfer_status, transfer_lock):
    """修复失效链接：搜索替代资源 → 验证 → 更新

    Args:
        transfer_status: 全局状态字典
        transfer_lock: 状态锁
    Returns:
        修复结果汇总
    """
    tid = get_ident()
    with transfer_lock:
        transfer_status.update({
            "running": True,
            "thread_id": tid,
            "summary": "fix_expired",
            "stop": False
        })
    try:
        expired = check_expired_tasks(transfer_status)
        if not expired:
            log("没有失效链接，无需修复")
            return {"total": 0, "fixed": 0, "failed": 0, "results": []}

        log("开始修复 {} 个失效链接".format(len(expired)))
        fixed = 0
        failed = 0
        results = []

        for task in expired:
            if transfer_status.get("stop"):
                log("修复已被用户终止")
                break
            taskname = task.get("taskname", "")
            log("搜索替换: {}".format(taskname))

            try:
                sr = search_pansou(taskname)
                if not sr:
                    log("  未找到替代资源")
                    failed += 1
                    results.append({"taskname": taskname, "status": "not_found", "msg": "未找到替代资源"})
                    continue

                chosen = sr[0]
                new_url = chosen.get("url", "")
                if not new_url:
                    log("  资源无有效链接")
                    failed += 1
                    results.append({"taskname": taskname, "status": "no_url", "msg": "资源无有效链接"})
                    continue

                valid, msg = validate_share_link(new_url)
                if not valid:
                    log("  新链接无效: {}".format(msg))
                    failed += 1
                    results.append({"taskname": taskname, "status": "invalid", "msg": msg})
                    continue

                success = update_expired_task(task, new_url)
                if success:
                    log("  ✅ 替换成功: {}".format(chosen.get("note", "")))
                    fixed += 1
                    results.append({"taskname": taskname, "status": "fixed", "msg": chosen.get("note", "")})
                else:
                    log("  ❌ 更新失败")
                    failed += 1
                    results.append({"taskname": taskname, "status": "update_fail", "msg": "更新失败"})

                time.sleep(2)
            except Exception as e:
                log("  ❌ 异常: {}".format(e))
                failed += 1
                results.append({"taskname": taskname, "status": "error", "msg": str(e)})

        log("修复完成: 成功 {} / 失败 {}".format(fixed, failed))
        return {"total": len(expired), "fixed": fixed, "failed": failed, "results": results}
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["thread_id"] = None
            transfer_status["stop"] = False
