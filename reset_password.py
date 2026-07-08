"""重置登录密码脚本

用法（Docker）:
  docker run --rm -v /opt/douban-history:/data/douban-history ghcr.io/ciweicc/dbauto:latest --reset-password 新密码

或在已运行的容器中:
  docker exec -it dbauto python reset_password.py 新密码
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_modules"))

from config import load_config, save_config
from auth import hash_auth_password


def reset_password(new_password):
    if not new_password:
        print("错误: 密码不能为空")
        sys.exit(1)
    if len(new_password) > 100:
        print("错误: 密码长度不能超过 100 个字符")
        sys.exit(1)

    cfg = load_config()
    cfg["auth_pass"] = hash_auth_password(new_password)
    save_config(cfg)
    print("密码已重置成功！请使用新密码登录。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python reset_password.py <新密码>")
        sys.exit(1)
    reset_password(sys.argv[1].strip())
