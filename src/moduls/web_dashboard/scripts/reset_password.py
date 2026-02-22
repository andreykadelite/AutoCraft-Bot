import os
import sys
from getpass import getpass
from pathlib import Path

from web_dashboard.config import load_config, update_user_password


def guess_base_dir() -> str:
    env = os.environ.get("PANEL_BASE_DIR")
    if env:
        return env
    if "NUITKA_ONEFILE_PARENT" in os.environ:
        return os.path.dirname(os.path.abspath(os.environ["NUITKA_ONEFILE_PARENT"]))
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return str(Path.cwd())


def main() -> None:
    base_dir = guess_base_dir()
    cfg = load_config(base_dir)

    while True:
        pwd1 = getpass("Введите новый пароль: ")
        pwd2 = getpass("Повторите пароль: ")
        if len(pwd1) < 6:
            print("Пароль слишком короткий (минимум 6 символов).")
            continue
        if pwd1 != pwd2:
            print("Пароли не совпадают.")
            continue
        update_user_password(cfg, "admin", pwd1, role="Super Admin", base_dir=base_dir)
        print("Пароль обновлён.")
        return


if __name__ == "__main__":
    main()
