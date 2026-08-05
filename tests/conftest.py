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
