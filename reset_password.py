"""重置登录密码脚本

安全说明：密码不再作为命令行参数传入（会泄露到 shell 历史 ~/.bash_history 与
进程列表 ps），改为从标准输入读取。

用法（Docker，管道传入）:
  echo '新密码' | docker run --rm -i -v /opt/douban-history:/data/douban-history ghcr.io/ciweicc/dbauto:latest --reset-password

交互式（不回显）:
  docker exec -it dbauto python reset_password.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_modules"))

from config import load_config, save_config
from auth import hash_auth_password


def _read_password():
    """从标准输入读取密码：非 tty（管道）读一行；tty 下不回显输入。"""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n").strip()
    try:
        import getpass
        return getpass.getpass("请输入新密码: ").strip()
    except Exception:
        return input("请输入新密码: ").strip()


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


def main():
    if len(sys.argv) > 1:
        print("错误: 请勿在命令行参数中直接传入密码（会泄露到 shell 历史 / 进程列表）。")
        print("请改为通过标准输入传入，例如：")
        print("  echo '新密码' | python reset_password.py")
        print("  或交互式运行：python reset_password.py  然后按提示输入")
        sys.exit(1)
    reset_password(_read_password())


if __name__ == "__main__":
    main()
