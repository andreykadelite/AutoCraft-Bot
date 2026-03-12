import os
import sys
from getpass import getpass
from pathlib import Path

from web_dashboard.config import create_panel_user, get_panel_bootstrap_state


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
    state = get_panel_bootstrap_state(base_dir)

    if bool(state.get("has_super_admin")):
        print("Первый Super Admin уже создан.")
        return

    default_login = "admin"
    while True:
        raw_login = input(f"Введите логин первого Super Admin [{default_login}]: ").strip()
        login = raw_login or default_login
        if len(login) < 3 or (" " in login):
            print("Логин должен быть минимум 3 символа и без пробелов.")
            continue

        pwd1 = getpass("Введите пароль: ")
        pwd2 = getpass("Повторите пароль: ")
        if len(pwd1) < 6:
            print("Пароль слишком короткий (минимум 6 символов).")
            continue
        if pwd1 != pwd2:
            print("Пароли не совпадают.")
            continue
        try:
            create_panel_user(base_dir, login, login, pwd1, role="Super Admin")
        except Exception as exc:
            print(f"Не удалось создать пользователя: {exc}")
            continue
        print(f"Пользователь {login} (Super Admin) создан.")
        return


if __name__ == "__main__":
    main()
