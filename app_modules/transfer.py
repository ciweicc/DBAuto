# transfer.py — 薄包装层：re-export 拆分后的模块接口
#
# 拆分说明：
#   search.py    — PanSou 搜索 + 缓存
#   dedup.py     — QAS 去重缓存 + 历史匹配
#   verify.py    — 链接有效性检测 + 失效修复
#   pipeline.py  — 编排调度（run_transfer, build_transfer_tasks 等）
#   transfer.py  — 本文件，保持对外接口不变
#
# 所有调用方（scheduler.py, routes_transfer.py, main.py 等）无需改动。

# ===== 从 pipeline.py re-export（状态管理 + 转存执行）=====
from pipeline import (
    transfer_status, transfer_lock,
    is_transfer_running,
    add_and_run, transfer_one,
    build_transfer_tasks, run_transfer,
    VIDEO_SUB, TV_REPLACE,
    SEARCH_CONCURRENCY,
)

# ===== 从 search.py re-export（搜索）=====
from search import search_pansou

# ===== 从 dedup.py re-export（去重 + QAS 缓存）=====
from dedup import (
    is_in_qas, add_to_qas,
    init_qas_cache, reset_qas_client,
    find_in_history, build_history_index,
)

# 向后兼容：旧测试引用 _find_in_history / _build_history_index
_find_in_history = find_in_history
_build_history_index = build_history_index

# ===== 从 verify.py re-export（失效检测 + 修复）=====
from verify import (
    validate_share_link,
    update_expired_task,
    EXPIRED_CHECK_CONCURRENCY,
)


def check_expired_tasks(limit=None):
    """薄包装：自动注入 transfer_status"""
    from verify import check_expired_tasks as _check
    return _check(transfer_status, limit)


def fix_expired_tasks():
    """薄包装：自动注入 transfer_status 和 transfer_lock"""
    from verify import fix_expired_tasks as _fix
    return _fix(transfer_status, transfer_lock)
