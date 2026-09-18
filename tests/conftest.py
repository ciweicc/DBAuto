"""
共享 pytest fixture：统一 sys.path 配置，确保所有测试文件都能导入 app_modules。
"""
import os
import sys

# 项目根目录：tests/ 的上一级
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_MODULES = os.path.join(_ROOT, "app_modules")

# 仓库根加入 sys.path：支持 `from scripts import reset_password` 等隐式命名空间包导入
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if _APP_MODULES not in sys.path:
    sys.path.insert(0, _APP_MODULES)


# ---------------------------------------------------------------- 缓存隔离
# 进程级缓存的隔离（issue #47「经常回落」修复引入）：
#   - openlist 新增「目录成功缓存 + 失败退避」（OPENLIST_CACHE_TTL /
#     OPENLIST_FAIL_BACKOFF），
#   - media_library 早就有「目录短命缓存」（LIBRARY_CACHE_TTL）。
# 这些缓存都是**进程级**的，跨用例会串：前一个用例的成功结果会让后一个
# 用例断言「失败」时被缓存挡掉（实测：test_missing_path_error_* 会假通过）。
# 这里统一在每个用例前清空，让每个用例都从「没有缓存」的干净状态开始 ——
# 缓存行为本身另有专门用例覆盖，不靠别的用例「顺手」留下状态。
import pytest


@pytest.fixture(autouse=True)
def _reset_module_caches():
    try:
        import openlist
        openlist.invalidate_dir_cache()
        openlist.reset_token_cache()
    except Exception:
        pass
    try:
        import media_library
        media_library.invalidate_library_cache()
    except Exception:
        pass
    yield
    try:
        import openlist
        openlist.invalidate_dir_cache()
        openlist.reset_token_cache()
    except Exception:
        pass
    try:
        import media_library
        media_library.invalidate_library_cache()
    except Exception:
        pass
