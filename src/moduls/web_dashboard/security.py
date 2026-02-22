import time
import functools
import random
import re
import threading
from typing import Dict, Iterable, List

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from flask_appbuilder.fieldwidgets import (
    BS3PasswordFieldWidget,
    Select2ManyWidget,
    Select2Widget,
)
from flask_appbuilder.fields import QuerySelectField, QuerySelectMultipleField
from flask_appbuilder.security import views as sec_views
from flask_appbuilder.security.decorators import PERMISSION_PREFIX
from flask_appbuilder.security.forms import DynamicForm
from flask_appbuilder.security.sqla.manager import SecurityManager
from flask_appbuilder.validators import PasswordComplexityValidator
from flask_login import current_user, login_user
from markupsafe import Markup, escape
from wtforms import PasswordField, validators
from wtforms.validators import EqualTo, ValidationError

from .db import db
from .login_progress import (
    create_login_progress,
    get_login_progress_payload,
    start_login_progress,
)
from .models.audit import AuditLog

MAX_LOGIN_ATTEMPTS = 5
LOGIN_FAILURE_RESET_SECONDS = 5 * 60
LOCKOUT_MIN_SECONDS = 60
LOCKOUT_MAX_SECONDS = 120

_login_attempts: Dict[str, List[float]] = {}
_login_locks: Dict[str, float] = {}
_login_rate_limit_lock = threading.RLock()

_ROLE_LABELS = {
    "Super Admin": "Суперадминистратор",
    "Admin": "Администратор",
    "Operator": "Оператор",
    "Viewer": "Наблюдатель",
    "Auditor": "Аудитор",
    "Public": "Публичная роль",
}

_PERMISSION_LABELS = {
    "can_action": "Выполнение действий",
    "can_add": "Создание",
    "can_chart": "Просмотр диаграмм",
    "can_delete": "Удаление",
    "can_download": "Скачивание",
    "can_edit": "Редактирование",
    "can_get": "Просмотр (API)",
    "can_index": "Открытие раздела",
    "can_list": "Просмотр списка",
    "can_overview_data": "Просмотр сводных данных",
    "can_show": "Просмотр записи",
    "can_this_form_get": "Открыть форму",
    "can_this_form_post": "Сохранить форму",
    "can_userinfo": "Просмотр профиля",
    "copyrole": "Копирование роли",
    "menu_access": "Доступ к меню",
    "resetmypassword": "Смена собственного пароля",
    "resetpasswords": "Смена пароля пользователя",
    "userinfoedit": "Редактирование профиля",
}

_VIEW_LABELS = {
    "WacIndexView": "Главная",
    "ServerView": "Серверы",
    "SettingsView": "Настройки",
    "ExtensionsView": "Менеджер расширений",
    "FileManagerView": "Файловый менеджер",
    "RemoteDesktopView": "Удаленный рабочий стол",
    "LiveStreamView": "Прямая трансляция",
    "EventLogsView": "Журналы Windows",
    "ServicesView": "Службы",
    "ProcessesView": "Процессы",
    "TasksView": "Задачи",
    "TerminalView": "Терминал",
    "RegistryEditorView": "Редактор реестра",
    "AutoStartView": "Автозапуск",
    "DeviceManagerView": "Диспетчер устройств",
    "InternalMessengerView": "Внутренний мессенджер",
    "CommunicationCenterView": "Центр коммуникаций",
    "AdminBroadcastView": "Рассылка",
    "MetricsView": "Метрики",
    "StorageView": "Хранилище",
    "NetworkingView": "Сеть",
    "JobView": "Журнал заданий",
    "AuditView": "Аудит",
    "AutoCraftStatusView": "Статус AutoCraft",
    "AutoCraftOpsView": "Операции AutoCraft",
    "RuUserDBModelView": "Пользователи",
    "RuRoleModelView": "Роли",
    "RuUserGroupModelView": "Группы",
    "RuPermissionModelView": "Основные разрешения",
    "RuViewMenuModelView": "Представления/меню",
    "RuPermissionViewModelView": "Права на представления/меню",
    "RuUserStatsChartView": "Статистика пользователей",
    "RuUserInfoEditView": "Редактирование профиля",
    "RuResetMyPasswordView": "Смена пароля",
    "RuResetPasswordView": "Смена пароля пользователя",
    "PanelAuthDBView": "Вход в панель",
    "List Users": "Пользователи",
    "List Roles": "Роли",
    "List Groups": "Группы",
    "User's Statistics": "Статистика пользователей",
    "User Registrations": "Заявки на регистрацию",
    "Base Permissions": "Основные разрешения",
    "Views/Menus": "Представления/меню",
    "Permission on Views/Menus": "Права на представления/меню",
    "Security": "Администрирование",
    "LocaleView": "Язык",
    "MenuApi": "API меню",
    "SecurityApi": "API безопасности",
    "UtilView": "Служебные действия",
    "WinRunView": "Win+R",
    "NotifyCenterView": "Уведомления",
    "SystemNotifyCenterView": "Уведомления AutoCraft",
}


def _get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    return request.headers.get("X-Real-IP", "") or request.remote_addr or ""


def _set_rate_limit_context(retry_after: int | None) -> None:
    if not retry_after:
        return
    try:
        g.login_blocked = True
        g.login_retry_after = int(retry_after)
    except Exception:
        pass


def _rate_limit_key(ip: str, username: str | None) -> str:
    user = (username or "").strip()
    if user:
        user = user.casefold()
        return f"{ip}|{user}" if ip else user
    return ip or ""


def _is_rate_limited(ip: str, username: str | None) -> tuple[bool, int]:
    key = _rate_limit_key(ip, username)
    if not key:
        return False, 0
    now = time.time()
    with _login_rate_limit_lock:
        lock_until = _login_locks.get(key)
        if lock_until:
            if now < lock_until:
                return True, max(1, int(lock_until - now))
            _login_locks.pop(key, None)

        attempts = _login_attempts.get(key, [])
        if attempts:
            attempts = [t for t in attempts if now - t <= LOGIN_FAILURE_RESET_SECONDS]
            if attempts:
                _login_attempts[key] = attempts
            else:
                _login_attempts.pop(key, None)
    return False, 0


def _register_failed_attempt(ip: str, username: str | None) -> int | None:
    key = _rate_limit_key(ip, username)
    if not key:
        return None
    now = time.time()
    with _login_rate_limit_lock:
        attempts = _login_attempts.setdefault(key, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts if now - t <= LOGIN_FAILURE_RESET_SECONDS]
        if len(attempts) >= MAX_LOGIN_ATTEMPTS:
            lock_for = random.randint(LOCKOUT_MIN_SECONDS, LOCKOUT_MAX_SECONDS)
            _login_locks[key] = now + lock_for
            _login_attempts.pop(key, None)
            return lock_for
    return None


def _clear_login_failures(ip: str, username: str | None) -> None:
    key = _rate_limit_key(ip, username)
    if not key:
        return
    with _login_rate_limit_lock:
        _login_attempts.pop(key, None)
        _login_locks.pop(key, None)


def _roles_custom_formatter_ru(text: str) -> str:
    if current_app.config.get("AUTH_ROLES_SYNC_AT_LOGIN", False):
        text += (
            ". <div class='alert alert-warning' role='alert'>"
            "AUTH_ROLES_SYNC_AT_LOGIN включен: изменения в этом поле будут "
            "сброшены при следующем входе пользователя."
            "</div>"
        )
    return text


def _roles_or_groups_required_ru(form, field) -> None:
    if not form["roles"].data and not form["groups"].data:
        raise ValidationError("Нужно выбрать роль или группу")


def _display_with_code(label: str | None, code: str) -> str:
    if not code:
        return ""
    if not label or label == code:
        return code
    return f"{label} ({code})"


def _format_role_name(role: object) -> str:
    name = getattr(role, "name", None) or str(role or "")
    label = _ROLE_LABELS.get(name, "")
    return _display_with_code(label, name)


def _format_group_name(group: object) -> str:
    label = getattr(group, "label", None) or ""
    name = getattr(group, "name", None) or str(group or "")
    if label and label != name:
        return f"{label} ({name})"
    return name or label


def _format_permission_name(name: str | None) -> str:
    if not name:
        return ""
    label = _PERMISSION_LABELS.get(name, "")
    return _display_with_code(label, name)


def _format_view_name(name: str | None) -> str:
    if not name:
        return ""
    label = _VIEW_LABELS.get(name, "")
    return _display_with_code(label, name)


def _format_permission_obj(permission: object) -> str:
    name = getattr(permission, "name", None) or str(permission or "")
    return _format_permission_name(name)


def _format_view_menu_obj(view_menu: object) -> str:
    name = getattr(view_menu, "name", None) or str(view_menu or "")
    return _format_view_name(name)


def _format_permission_view_obj(permission_view: object) -> str:
    if not permission_view:
        return ""
    perm = _format_permission_obj(getattr(permission_view, "permission", None))
    view = _format_view_menu_obj(getattr(permission_view, "view_menu", None))
    if perm and view:
        return f"{perm} - {view}"
    return perm or view


_PERMISSION_HINTS = {
    "menu_access": "Доступ к пункту меню. Без него раздел не отображается в навигации.",
    "can_list": "Открывает список или основную страницу раздела.",
    "can_index": "Открывает стартовую страницу раздела.",
    "can_show": "Просмотр отдельной записи. Обычно требует доступ к списку.",
    "can_add": "Создание новых записей.",
    "can_edit": "Редактирование записей. Обычно требует доступ к списку.",
    "can_delete": "Удаление записей. Обычно требует доступ к списку.",
    "can_action": "Выполнение действий в разделе (запуск, остановка, операции). Требует доступ к списку.",
    "can_chart": "Просмотр диаграмм и графиков.",
    "can_download": "Скачивание данных.",
    "can_get": "Просмотр данных через API.",
    "can_overview_data": "Просмотр сводной информации.",
    "can_this_form_get": "Открытие формы.",
    "can_this_form_post": "Сохранение формы.",
    "can_userinfo": "Просмотр профиля пользователя.",
    "copyrole": "Создание копии выбранной роли.",
    "resetmypassword": "Смена собственного пароля пользователем.",
    "resetpasswords": "Смена паролей других пользователей.",
    "userinfoedit": "Редактирование профиля пользователя.",
}

_PERMISSION_META = {
    "can_list": {"base": True},
    "can_index": {"base": True},
    "can_show": {"requires_base": True},
    "can_add": {"requires_base": True},
    "can_edit": {"requires_base": True},
    "can_delete": {"requires_base": True},
    "can_action": {"requires_base": True},
}

_PERMISSION_ORDER = {
    "menu_access": 0,
    "can_index": 1,
    "can_list": 2,
    "can_show": 3,
    "can_add": 4,
    "can_edit": 5,
    "can_delete": 6,
    "can_action": 7,
    "can_chart": 8,
    "can_download": 9,
    "can_get": 10,
    "can_overview_data": 11,
    "can_this_form_get": 12,
    "can_this_form_post": 13,
    "can_userinfo": 14,
    "copyrole": 15,
    "resetmypassword": 16,
    "resetpasswords": 17,
    "userinfoedit": 18,
}


def _safe_dom_id(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_-]+", "_", value).strip("_")


def _build_menu_maps():
    appbuilder = getattr(current_app, "appbuilder", None)
    menu = getattr(appbuilder, "menu", None) if appbuilder else None
    if not menu:
        return {}, {}, {}, set()

    view_to_menu: dict[str, str] = {}
    view_to_category: dict[str, str] = {}
    label_to_view: dict[str, str] = {}
    categories: set[str] = set()

    for category in getattr(menu, "menu", []) or []:
        category_label = getattr(category, "label", None) or getattr(category, "name", None)
        if category_label:
            categories.add(category_label)
        for item in getattr(category, "childs", []) or []:
            baseview = getattr(item, "baseview", None)
            if not baseview:
                continue
            view_name = getattr(baseview, "class_permission_name", None) or baseview.__class__.__name__
            item_label = getattr(item, "label", None) or getattr(item, "name", None) or view_name
            view_to_menu[view_name] = item_label
            if category_label:
                view_to_category[view_name] = category_label
            if item_label:
                label_to_view[item_label] = view_name
            item_name = getattr(item, "name", None)
            if item_name:
                label_to_view[item_name] = view_name

    return view_to_menu, view_to_category, label_to_view, categories


class PermissionConstructorWidget:
    def __call__(self, field, **kwargs):
        try:
            object_list = list(field._get_object_list())
        except Exception:
            object_list = []

        selected_pks: set[str] = set()
        try:
            for obj in field.data or []:
                selected_pks.add(str(field.get_pk_func(obj)))
        except Exception:
            selected_pks = set()

        view_to_menu, view_to_category, label_to_view, category_labels = _build_menu_maps()

        categories: dict[str, dict] = {}
        uncategorized_label = "Без категории"

        def _get_category(label: str | None):
            name = label or uncategorized_label
            return categories.setdefault(
                name,
                {"label": name, "menu_access": None, "views": {}},
            )

        def _get_view(category_label: str | None, view_name: str, view_label: str):
            category = _get_category(category_label)
            return category["views"].setdefault(
                view_name,
                {
                    "name": view_name,
                    "label": view_label,
                    "menu_access": None,
                    "permissions": [],
                },
            )

        for pk, obj in object_list:
            perm_name = getattr(getattr(obj, "permission", None), "name", "") or ""
            view_menu_name = getattr(getattr(obj, "view_menu", None), "name", "") or ""
            selected = str(pk) in selected_pks

            if perm_name == "menu_access":
                if view_menu_name in category_labels:
                    category = _get_category(view_menu_name)
                    category["menu_access"] = {"pk": pk, "selected": selected}
                    continue
                mapped_view = label_to_view.get(view_menu_name)
                if mapped_view:
                    view_name = mapped_view
                else:
                    view_name = view_menu_name
                category_label = view_to_category.get(view_name)
                view_label = view_to_menu.get(view_name) or _VIEW_LABELS.get(view_name) or view_menu_name
                view = _get_view(category_label, view_name, view_label)
                view["menu_access"] = {"pk": pk, "selected": selected}
                continue

            view_name = view_menu_name
            category_label = view_to_category.get(view_name)
            view_label = view_to_menu.get(view_name) or _VIEW_LABELS.get(view_name) or view_menu_name
            view = _get_view(category_label, view_name, view_label)
            view["permissions"].append(
                {
                    "pk": pk,
                    "perm": perm_name,
                    "selected": selected,
                }
            )

        def _perm_sort_key(item):
            perm = item.get("perm") or ""
            return (_PERMISSION_ORDER.get(perm, 999), perm)

        for category in categories.values():
            for view in category["views"].values():
                view["permissions"].sort(key=_perm_sort_key)

        def _render_checkbox(pk, name, label, hint, selected, attrs=None):
            input_id = f"{field.id}_{_safe_dom_id(str(pk))}"
            hint_id = f"{input_id}_hint"
            classes = "perm-check"
            input_attrs = {
                "type": "checkbox",
                "id": input_id,
                "name": field.name,
                "value": str(pk),
                "aria-describedby": hint_id if hint else None,
            }
            if selected:
                input_attrs["checked"] = "checked"
            if attrs:
                input_attrs.update(attrs)

            attrs_html = " ".join(
                f'{key}="{escape(str(value))}"'
                for key, value in input_attrs.items()
                if value is not None and value != ""
            )
            input_html = f"<input {attrs_html}>"
            label_html = f"<span class=\"perm-label\">{escape(label)}</span>"
            hint_html = (
                f"<span class=\"perm-hint\" id=\"{escape(hint_id)}\">{escape(hint)}</span>"
                if hint
                else ""
            )
            return (
                f"<div class=\"perm-item\">"
                f"<label class=\"{classes}\" for=\"{escape(input_id)}\">"
                f"{input_html}{label_html}</label>"
                f"{hint_html}</div>"
            )

        def _render_permission_item(view, item):
            perm = item.get("perm") or ""
            pk = item.get("pk")
            selected = bool(item.get("selected"))
            meta = _PERMISSION_META.get(perm, {})
            label = _PERMISSION_LABELS.get(perm) or perm
            hint = _PERMISSION_HINTS.get(perm, "")
            attrs = {
                "data-perm-name": perm,
                "data-perm-scope": "view",
            }
            if meta.get("base"):
                attrs["data-perm-base"] = "1"
            if meta.get("requires_base"):
                attrs["data-perm-requires-base"] = "1"
            return _render_checkbox(pk, field.name, label, hint, selected, attrs=attrs)

        def _render_menu_access(pk, selected, scope, label, hint):
            attrs = {
                "data-perm-name": "menu_access",
                "data-perm-scope": scope,
                "data-perm-menu": "1",
            }
            return _render_checkbox(pk, field.name, label, hint, selected, attrs=attrs)

        help_text = (
            "Отмечайте права для разделов. Для появления пункта меню нужен доступ к категории и "
            "к самому пункту. Для работы раздела обычно требуется «Просмотр списка» или "
            "«Открытие раздела». «Выполнение действий» работает только вместе с просмотром."
        )

        parts = [
            "<div class=\"perm-builder\" data-perm-builder=\"1\">",
            f"<div class=\"perm-builder-help\">{escape(help_text)}</div>",
            "<div class=\"perm-builder-status sr-only\" role=\"status\" aria-live=\"polite\"></div>",
        ]

        for category_label in sorted(
            categories.keys(),
            key=lambda value: (value == uncategorized_label, value.lower()),
        ):
            category = categories[category_label]
            parts.append("<section class=\"perm-category\">")
            parts.append(
                f"<div class=\"perm-category-header\">"
                f"<h4 class=\"perm-category-title\">{escape(category_label)}</h4>"
            )
            if category.get("menu_access"):
                menu_access = category["menu_access"]
                parts.append(
                    _render_menu_access(
                        menu_access["pk"],
                        menu_access["selected"],
                        "category",
                        "Меню (категория)",
                        "Показывает категорию меню. Без нее пункты раздела скрыты.",
                    )
                )
            parts.append("</div>")

            for view in sorted(category["views"].values(), key=lambda item: item["label"].lower()):
                parts.append("<div class=\"perm-view\">")
                parts.append(
                    f"<div class=\"perm-view-header\">"
                    f"<h5 class=\"perm-view-title\">{escape(view['label'])}</h5>"
                    f"</div>"
                )
                parts.append("<div class=\"perm-items\" role=\"group\" aria-label=\"Права раздела\">")
                if view.get("menu_access"):
                    menu_access = view["menu_access"]
                    parts.append(
                        _render_menu_access(
                            menu_access["pk"],
                            menu_access["selected"],
                            "view",
                            "Меню (пункт)",
                            "Показывает пункт меню для этого раздела.",
                        )
                    )
                for item in view.get("permissions", []):
                    parts.append(_render_permission_item(view, item))
                parts.append("</div></div>")

            parts.append("</section>")

        parts.append("</div>")
        return Markup("".join(parts))


class ChecklistWidget:
    def __call__(self, field, **kwargs):
        choices = list(field.iter_choices())
        if not choices:
            return Markup("<div class=\"checklist-empty\">Нет доступных вариантов.</div>")

        group_label = field.label.text if getattr(field, "label", None) else ""
        group_attr = f' aria-label="{escape(group_label)}"' if group_label else ""
        parts = [f"<div class=\"checklist\" role=\"group\"{group_attr}>"]

        for choice in choices:
            if len(choice) == 4:
                value, label, selected, option_kwargs = choice
            else:
                value, label, selected = choice
                option_kwargs = {}

            input_id = f"{field.id}_{_safe_dom_id(str(value))}"
            input_attrs = {
                "type": "checkbox",
                "id": input_id,
                "name": field.name,
                "value": str(value),
            }
            if selected:
                input_attrs["checked"] = "checked"
            if isinstance(option_kwargs, dict) and option_kwargs.get("disabled"):
                input_attrs["disabled"] = "disabled"

            attrs_html = " ".join(
                f'{key}="{escape(str(val))}"'
                for key, val in input_attrs.items()
                if val is not None and val != ""
            )
            label_text = escape(str(label))
            parts.append(
                f"<label class=\"checklist-item\" for=\"{escape(input_id)}\">"
                f"<input {attrs_html}>"
                f"<span class=\"checklist-label\">{label_text}</span>"
                f"</label>"
            )

        parts.append("</div>")
        return Markup("".join(parts))

def _format_list(items: Iterable, item_formatter) -> Markup:
    if not items:
        return Markup("")
    parts = []
    for item in items:
        text = item_formatter(item)
        if text:
            parts.append(escape(text))
    if not parts:
        return Markup("")
    return Markup("<br>").join(parts)


def _resolve_view_label(view_obj) -> str:
    view_name = getattr(view_obj, "class_permission_name", None) or view_obj.__class__.__name__
    label = _VIEW_LABELS.get(view_name)
    if label:
        return label
    menu = getattr(getattr(view_obj, "appbuilder", None), "menu", None)
    for category in getattr(menu, "menu", []) if menu else []:
        for item in getattr(category, "childs", []) if category else []:
            baseview = getattr(item, "baseview", None)
            if not baseview:
                continue
            if baseview is view_obj or type(baseview).__name__ == view_name:
                return getattr(item, "label", None) or getattr(item, "name", None) or view_name
    return view_name


def _access_denied_payload(view_obj, permission_str: str) -> dict:
    view_name = getattr(view_obj, "class_permission_name", None) or view_obj.__class__.__name__
    view_label = _resolve_view_label(view_obj)
    view_display = _display_with_code(view_label, view_name)
    perm_label = _PERMISSION_LABELS.get(permission_str)
    perm_display = _display_with_code(perm_label, permission_str)
    message = f"Доступ к разделу \"{view_label}\" запрещен."
    return {
        "message": message,
        "view_label": view_label,
        "view_name": view_name,
        "view_display": view_display,
        "permission_label": perm_label or permission_str,
        "permission_name": permission_str,
        "permission_display": perm_display,
        "request_path": request.path,
    }


def panel_has_access(f):
    """
    Аналог has_access, но возвращает 403 с объяснением вместо редиректа.
    """
    permission_str = getattr(f, "_permission_name", None) or f.__name__

    def wraps(self, *args, **kwargs):
        permission_str = f"{PERMISSION_PREFIX}{f._permission_name}"
        if self.method_permission_name:
            override = self.method_permission_name.get(f.__name__)
            if override:
                permission_str = f"{PERMISSION_PREFIX}{override}"
        if permission_str in self.base_permissions and self.appbuilder.sm.has_access(
            permission_str, self.class_permission_name
        ):
            return f(self, *args, **kwargs)

        try:
            current_app.logger.warning(
                "Доступ запрещен: %s на %s",
                permission_str,
                self.__class__.__name__,
            )
        except Exception:
            pass

        if not current_user.is_authenticated:
            return redirect(
                url_for(
                    self.appbuilder.sm.auth_view.__class__.__name__ + ".login",
                    next=request.url,
                )
            )

        payload = _access_denied_payload(self, permission_str)
        return self.render_template("access_denied.html", **payload), 403

    f._permission_name = permission_str
    return functools.update_wrapper(wraps, f)


def panel_has_access_api(f):
    """
    Аналог has_access_api с детальным русским ответом.
    """
    permission_str = getattr(f, "_permission_name", None) or f.__name__

    def wraps(self, *args, **kwargs):
        permission_str = f"{PERMISSION_PREFIX}{f._permission_name}"
        if self.method_permission_name:
            override = self.method_permission_name.get(f.__name__)
            if override:
                permission_str = f"{PERMISSION_PREFIX}{override}"
        if permission_str in self.base_permissions and self.appbuilder.sm.has_access(
            permission_str, self.class_permission_name
        ):
            return f(self, *args, **kwargs)

        try:
            current_app.logger.warning(
                "Доступ запрещен (API): %s на %s",
                permission_str,
                self.__class__.__name__,
            )
        except Exception:
            pass

        if not current_user.is_authenticated:
            return jsonify({"message": "Требуется авторизация."}), 401

        payload = _access_denied_payload(self, permission_str)
        return jsonify(payload), 403

    f._permission_name = permission_str
    return functools.update_wrapper(wraps, f)


def _build_relation_select(datamodel, col_name: str, label: str, get_label):
    try:
        rel = datamodel.get_related_interface(col_name)
    except Exception:
        return None
    return QuerySelectField(
        label,
        query_func=lambda: rel.query()[1],
        get_pk_func=lambda obj: rel.get_pk_value(obj),
        get_label=get_label,
        allow_blank=datamodel.is_nullable(col_name),
        widget=Select2Widget(),
    )


def _build_relation_multiselect(datamodel, col_name: str, label: str, get_label):
    try:
        rel = datamodel.get_related_interface(col_name)
    except Exception:
        return None
    return QuerySelectMultipleField(
        label,
        query_func=lambda: rel.query()[1],
        get_pk_func=lambda obj: rel.get_pk_value(obj),
        get_label=get_label,
        widget=Select2ManyWidget(),
    )


def _build_permission_constructor(datamodel, col_name: str, label: str, get_label):
    try:
        rel = datamodel.get_related_interface(col_name)
    except Exception:
        return None
    return QuerySelectMultipleField(
        label,
        query_func=lambda: rel.query()[1],
        get_pk_func=lambda obj: rel.get_pk_value(obj),
        get_label=get_label,
        widget=PermissionConstructorWidget(),
    )


def _build_relation_checklist(datamodel, col_name: str, label: str, get_label):
    try:
        rel = datamodel.get_related_interface(col_name)
    except Exception:
        return None
    return QuerySelectMultipleField(
        label,
        query_func=lambda: rel.query()[1],
        get_pk_func=lambda obj: rel.get_pk_value(obj),
        get_label=get_label,
        widget=ChecklistWidget(),
    )


class RuPasswordComplexityValidator(PasswordComplexityValidator):
    def __call__(self, form, field) -> None:
        try:
            super().__call__(form, field)
        except ValidationError:
            raise ValidationError(
                "Пароль должен быть сложным: минимум 10 символов, две заглавные, "
                "три строчные, две цифры и один спецсимвол."
            )


class RuCrudMessagesMixin:
    add_row_message = "Запись добавлена"
    edit_row_message = "Запись обновлена"
    delete_row_message = "Запись удалена"
    delete_integrity_error_message = "Есть связанные данные, удалите их сначала"
    add_integrity_error_message = "Ошибка целостности, возможно запись уже существует"
    edit_integrity_error_message = "Ошибка целостности, возможно запись уже существует"
    database_error_message = "Ошибка базы данных"


class RuUserInfoEditView(sec_views.UserInfoEditView):
    form_title = "Редактирование профиля"
    message = "Данные пользователя обновлены"


class RuUserDBModelView(RuCrudMessagesMixin, sec_views.UserDBModelView):
    list_title = "Пользователи"
    show_title = "Пользователь"
    add_title = "Добавить пользователя"
    edit_title = "Редактировать пользователя"

    label_columns = {
        "get_full_name": "Полное имя",
        "first_name": "Имя",
        "last_name": "Фамилия",
        "username": "Логин",
        "password": "Пароль",
        "active": "Активен",
        "email": "Эл. почта",
        "roles": "Роли",
        "groups": "Группы",
        "last_login": "Последний вход",
        "login_count": "Количество входов",
        "fail_login_count": "Неудачных входов",
        "created_on": "Создан",
        "created_by": "Создал",
        "changed_on": "Изменен",
        "changed_by": "Изменил",
    }

    description_columns = dict(sec_views.UserModelView.description_columns)
    description_columns.update(
        {
            "first_name": "Введите имя пользователя",
            "last_name": "Введите фамилию пользователя",
            "username": "Логин для входа в систему",
            "password": "Пароль пользователя для входа",
            "active": "Рекомендуется отключать пользователя вместо удаления",
            "email": "Адрес электронной почты пользователя",
            "roles": sec_views.lazy_formatter_gettext(
                "Роли пользователя определяют набор прав в системе",
                _roles_custom_formatter_ru,
            ),
            "groups": sec_views.lazy_formatter_gettext(
                "Группы содержат набор ролей для пользователя",
                _roles_custom_formatter_ru,
            ),
            "conf_password": "Повторите пароль для подтверждения",
        }
    )
    user_info_title = "Мой профиль"

    show_fieldsets = [
        (
            "Учетная запись",
            {"fields": ["username", "active", "roles", "login_count"]},
        ),
        (
            "Личные данные",
            {"fields": ["first_name", "last_name", "email"], "expanded": True},
        ),
        (
            "Аудит",
            {
                "fields": [
                    "last_login",
                    "fail_login_count",
                    "created_on",
                    "created_by",
                    "changed_on",
                    "changed_by",
                ],
                "expanded": False,
            },
        ),
    ]

    user_show_fieldsets = [
        (
            "Учетная запись",
            {"fields": ["username", "active", "roles", "login_count"]},
        ),
        (
            "Личные данные",
            {"fields": ["first_name", "last_name", "email"], "expanded": True},
        ),
    ]

    add_form_extra_fields = {
        "password": PasswordField(
            "Пароль",
            description="Пароль для входа в систему",
            validators=[
                validators.DataRequired(message="Введите пароль"),
                RuPasswordComplexityValidator(),
            ],
            widget=BS3PasswordFieldWidget(),
        ),
        "conf_password": PasswordField(
            "Подтверждение пароля",
            description="Повторите пароль для подтверждения",
            validators=[
                validators.DataRequired(message="Подтвердите пароль"),
                EqualTo("password", message="Пароли должны совпадать"),
            ],
            widget=BS3PasswordFieldWidget(),
        ),
    }
    validators_columns = {
        "roles": [_roles_or_groups_required_ru],
        "groups": [_roles_or_groups_required_ru],
    }
    formatters_columns = {
        "roles": lambda value: _format_list(value, _format_role_name),
        "groups": lambda value: _format_list(value, _format_group_name),
    }

    def _init_forms(self):
        role_field = _build_relation_checklist(
            self.datamodel,
            "roles",
            "Роли",
            _format_role_name,
        )
        group_field = _build_relation_checklist(
            self.datamodel,
            "groups",
            "Группы",
            _format_group_name,
        )
        if role_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["roles"] = role_field
            self.edit_form_extra_fields["roles"] = role_field
        if group_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["groups"] = group_field
            self.edit_form_extra_fields["groups"] = group_field
        super()._init_forms()

    @sec_views.action(
        "resetmypassword",
        "Сменить мой пароль",
        "",
        "fa-lock",
        multiple=False,
    )
    def resetmypassword(self, item):
        return redirect(
            url_for(self.appbuilder.sm.resetmypasswordview.__name__ + ".this_form_get")
        )

    @sec_views.action(
        "resetpasswords",
        "Сменить пароль",
        "",
        "fa-lock",
        multiple=False,
    )
    def resetpasswords(self, item):
        return redirect(
            url_for(
                self.appbuilder.sm.resetpasswordview.__name__ + ".this_form_get",
                pk=item.id,
            )
        )

    @sec_views.action(
        "userinfoedit",
        "Редактировать профиль",
        "",
        "fa-edit",
        multiple=False,
    )
    def userinfoedit(self, item):
        return redirect(
            url_for(self.appbuilder.sm.userinfoeditview.__name__ + ".this_form_get")
        )


class RuUserGroupModelView(RuCrudMessagesMixin, sec_views.UserGroupModelView):
    list_title = "Группы"
    show_title = "Группа"
    add_title = "Добавить группу"
    edit_title = "Редактировать группу"
    label_columns = {
        "name": "Имя",
        "label": "Название",
        "description": "Описание",
        "users": "Пользователи",
        "roles": "Роли",
    }
    formatters_columns = {
        "roles": lambda value: _format_list(value, _format_role_name),
        "users": lambda value: _format_list(value, lambda u: str(u or "")),
    }

    def _init_forms(self):
        role_field = _build_relation_checklist(
            self.datamodel,
            "roles",
            "Роли",
            _format_role_name,
        )
        if role_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["roles"] = role_field
            self.edit_form_extra_fields["roles"] = role_field
        super()._init_forms()

    def pre_delete(self, item):
        if item.users:
            self.update_redirect()
            raise sec_views.DeleteGroupWithUsersException(
                "В группе есть пользователи, удалить нельзя"
            )


class RuRoleModelView(RuCrudMessagesMixin, sec_views.RoleModelView):
    list_title = "Роли"
    show_title = "Роль"
    add_title = "Добавить роль"
    edit_title = "Редактировать роль"

    label_columns = {"name": "Имя", "permissions": "Права"}
    list_columns = ["name", "permissions"]
    show_columns = ["name", "permissions"]
    edit_columns = ["name", "permissions"]
    add_columns = edit_columns
    order_columns = ["name"]
    description_columns = {
        "name": "Имя роли, используется в настройках доступа",
        "permissions": (
            "Права доступа, закрепленные за ролью. "
            "Для появления раздела в меню нужны «Доступ к меню» для категории и "
            "для самого пункта, а также право на раздел "
            "(обычно «Просмотр списка» или «Открытие раздела»). "
            "Для выполнения действий добавьте «Выполнение действий»."
        ),
    }
    formatters_columns = {
        "name": lambda value: _display_with_code(_ROLE_LABELS.get(value), value or ""),
        "permissions": lambda value: _format_list(value, _format_permission_view_obj),
    }

    def _init_forms(self):
        perm_field = _build_permission_constructor(
            self.datamodel,
            "permissions",
            "Права",
            _format_permission_view_obj,
        )
        if perm_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["permissions"] = perm_field
            self.edit_form_extra_fields["permissions"] = perm_field
        super()._init_forms()

    @sec_views.action(
        "copyrole",
        "Копировать роль",
        "Скопировать выбранные роли?",
        icon="fa-copy",
        single=False,
    )
    def copy_role(self, items):
        self.update_redirect()
        for item in items:
            new_role = item.__class__()
            new_role.name = f"{item.name} копия"
            new_role.permissions = item.permissions
            self.datamodel.add(new_role)
        return redirect(self.get_redirect())

    def pre_delete(self, item):
        if item.user:
            self.update_redirect()
            raise sec_views.DeleteRoleWithUsersException(
                "В роли есть пользователи, удалить нельзя"
            )


class RuPermissionModelView(RuCrudMessagesMixin, sec_views.PermissionModelView):
    list_title = "Основные разрешения"
    show_title = "Основное разрешение"
    add_title = "Добавить разрешение"
    edit_title = "Редактировать разрешение"
    label_columns = {"name": "Право"}
    description_columns = {"name": "Операция, которую может выполнять пользователь"}
    formatters_columns = {"name": lambda value: _format_permission_name(value)}


class RuViewMenuModelView(RuCrudMessagesMixin, sec_views.ViewMenuModelView):
    list_title = "Представления/меню"
    show_title = "Представление/меню"
    add_title = "Добавить представление/меню"
    edit_title = "Редактировать представление/меню"
    label_columns = {"name": "Раздел/меню"}
    description_columns = {"name": "Раздел, для которого назначаются права"}
    formatters_columns = {"name": lambda value: _format_view_name(value)}


class RuPermissionViewModelView(RuCrudMessagesMixin, sec_views.PermissionViewModelView):
    list_title = "Права на представления/меню"
    show_title = "Право на представление/меню"
    add_title = "Добавить право"
    edit_title = "Редактировать право"
    label_columns = {"permission": "Право", "view_menu": "Раздел/меню"}
    description_columns = {
        "permission": "Операция, которая разрешена",
        "view_menu": "Раздел, к которому применяется право",
    }
    formatters_columns = {
        "permission": lambda value: _format_permission_obj(value),
        "view_menu": lambda value: _format_view_menu_obj(value),
    }

    def _init_forms(self):
        perm_field = _build_relation_select(
            self.datamodel,
            "permission",
            "Право",
            _format_permission_obj,
        )
        view_field = _build_relation_select(
            self.datamodel,
            "view_menu",
            "Раздел/меню",
            _format_view_menu_obj,
        )
        if perm_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["permission"] = perm_field
            self.edit_form_extra_fields["permission"] = perm_field
        if view_field:
            self.add_form_extra_fields = dict(self.add_form_extra_fields or {})
            self.edit_form_extra_fields = dict(self.edit_form_extra_fields or {})
            self.add_form_extra_fields["view_menu"] = view_field
            self.edit_form_extra_fields["view_menu"] = view_field
        super()._init_forms()


class RuUserStatsChartView(sec_views.UserStatsChartView):
    chart_title = "Статистика пользователей"
    label_columns = {
        "username": "Логин",
        "login_count": "Количество входов",
        "fail_login_count": "Неудачных входов",
    }
    definitions = [
        {"label": "Количество входов", "group": "username", "series": ["login_count"]},
        {
            "label": "Неудачные входы",
            "group": "username",
            "series": ["fail_login_count"],
        },
    ]


class RuRegisterUserModelView(RuCrudMessagesMixin, sec_views.RegisterUserModelView):
    list_title = "Заявки на регистрацию"
    show_title = "Заявка на регистрацию"
    list_columns = ["username", "registration_date", "email"]
    label_columns = {
        "username": "Логин",
        "registration_date": "Дата заявки",
        "email": "Эл. почта",
    }


class RuResetPasswordForm(DynamicForm):
    password = PasswordField(
        "Пароль",
        description="Используйте надежный пароль",
        validators=[
            validators.DataRequired(message="Введите пароль"),
            RuPasswordComplexityValidator(),
        ],
        widget=BS3PasswordFieldWidget(),
    )
    conf_password = PasswordField(
        "Подтверждение пароля",
        description="Повторите пароль для подтверждения",
        validators=[EqualTo("password", message="Пароли должны совпадать")],
        widget=BS3PasswordFieldWidget(),
    )


class RuResetMyPasswordView(sec_views.ResetMyPasswordView):
    form = RuResetPasswordForm
    form_title = "Смена пароля"
    message = "Пароль обновлен"


class RuResetPasswordView(sec_views.ResetPasswordView):
    form = RuResetPasswordForm
    form_title = "Смена пароля пользователя"
    message = "Пароль обновлен"


def move_security_menu_to_admin(appbuilder, target_category: str = "Администрирование") -> None:
    menu = getattr(appbuilder, "menu", None)
    if not menu:
        return
    security_category = menu.find("Security")
    if not security_category:
        return

    target = menu.find(target_category)
    if not target:
        menu.add_category(target_category, label=target_category)
        target = menu.find(target_category)
        if not target:
            return

    try:
        target.label = target_category
    except Exception:
        pass

    target_names = {item.name for item in target.childs}
    moved: list = []
    for item in list(security_category.childs):
        if item.name not in target_names:
            target.childs.append(item)
            target_names.add(item.name)
        moved.append(item)

    if moved:
        security_category.childs = [
            item for item in security_category.childs if item not in moved
        ]

    if not security_category.childs:
        try:
            menu.menu.remove(security_category)
        except ValueError:
            pass


def _get_login_admin_message() -> dict | None:
    try:
        from .models.admin_broadcast import AdminLoginBanner
    except Exception:
        return None

    try:
        latest = (
            db.session.query(AdminLoginBanner)
            .order_by(AdminLoginBanner.id.desc())
            .first()
        )
    except Exception:
        return None

    if not latest or not latest.enabled:
        return None

    subject = (latest.subject or "").strip() or "Сообщение администратора"
    created_at = latest.updated_at.strftime("%d.%m.%Y %H:%M") if latest.updated_at else ""
    return {
        "id": int(latest.id),
        "subject": subject,
        "body": latest.body or "",
        "created_at": created_at,
        "author": latest.updated_by_username or "",
    }


class PanelAuthDBView(sec_views.AuthDBView):
    login_template = "appbuilder/general/security/login_db.html"
    progress_template = "appbuilder/general/security/login_progress.html"
    title = "\u0412\u0445\u043e\u0434 \u0432 \u043f\u0430\u043d\u0435\u043b\u044c"
    invalid_login_message = (
        "\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043b\u043e\u0433\u0438\u043d "
        "\u0438\u043b\u0438 \u043f\u0430\u0440\u043e\u043b\u044c."
    )

    @sec_views.expose("/login/", methods=["GET", "POST"])
    @sec_views.no_cache
    def login(self):
        if g.user is not None and g.user.is_authenticated:
            return redirect(self.appbuilder.get_url_for_index)
        form = sec_views.LoginForm_db()
        attempted_username = ""
        if request.method == "POST":
            attempted_username = (form.username.data or "").strip()
            if attempted_username:
                session["login_last_username"] = attempted_username
        else:
            attempted_username = (
                request.args.get("username")
                or session.get("login_last_username")
                or ""
            ).strip()
        ip = _get_client_ip()
        limited, retry_after = (False, 0)
        if attempted_username:
            limited, retry_after = _is_rate_limited(ip, attempted_username)
        if limited:
            _set_rate_limit_context(retry_after)
        if form.validate_on_submit():
            next_url = sec_views.get_safe_redirect(request.args.get("next", ""))
            user = self.appbuilder.sm.auth_user_db(
                form.username.data, form.password.data
            )
            if not user:
                retry_after = getattr(g, "login_retry_after", None)
                if retry_after:
                    flash(
                        f"\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u043c\u043d\u043e\u0433\u043e "
                        f"\u043d\u0435\u0443\u0434\u0430\u0447\u043d\u044b\u0445 \u043f\u043e\u043f\u044b\u0442\u043e\u043a "
                        f"\u0432\u0445\u043e\u0434\u0430. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 "
                        f"\u0447\u0435\u0440\u0435\u0437 {retry_after} \u0441\u0435\u043a.",
                        "warning",
                    )
                else:
                    flash(self.invalid_login_message, "warning")
                return redirect(self.appbuilder.get_url_for_login_with(next_url))
            login_user(user, remember=False)
            session["panel_boot_id"] = current_app.config.get("SESSION_BOOT_ID")
            next_url = self.appbuilder.get_url_for_index
            token = create_login_progress(
                current_app._get_current_object(), user, next_url
            )
            session["panel_login_progress_token"] = token
            return redirect(next_url)
        device_name = current_app.config.get("DEVICE_NAME") or ""
        admin_login_message = _get_login_admin_message()
        return self.render_template(
            self.login_template,
            title=self.title,
            form=form,
            appbuilder=self.appbuilder,
            device_name=device_name,
            admin_login_message=admin_login_message,
            login_blocked=bool(getattr(g, "login_retry_after", 0)),
            login_retry_after=getattr(g, "login_retry_after", 0),
            blocked_username=attempted_username,
        )

    @sec_views.expose("/login/progress/")
    @sec_views.no_cache
    def login_progress(self):
        if not current_user.is_authenticated:
            return redirect(self.appbuilder.get_url_for_login)
        token = request.args.get("token", "")
        progress = get_login_progress_payload(token, current_user.id)
        if not progress:
            flash(
                "\u0414\u0430\u043d\u043d\u044b\u0435 \u0432\u0445\u043e\u0434\u0430 "
                "\u0443\u0441\u0442\u0430\u0440\u0435\u043b\u0438. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 "
                "\u0430\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044e.",
                "warning",
            )
            return redirect(self.appbuilder.get_url_for_index)
        start_login_progress(current_app._get_current_object(), token)
        return self.render_template(
            self.progress_template,
            title="\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043a\u0430 \u0432\u0445\u043e\u0434\u0430",
            progress=progress,
            token=token,
            next_url=progress.get("next_url"),
            appbuilder=self.appbuilder,
        )

    @sec_views.expose("/login/progress/status/<token>")
    @sec_views.no_cache
    def login_progress_status(self, token):
        if not current_user.is_authenticated:
            return jsonify({"error": "unauthorized"}), 401
        progress = get_login_progress_payload(token, current_user.id)
        if not progress:
            return jsonify({"error": "not_found"}), 404
        start_login_progress(current_app._get_current_object(), token)
        return jsonify(progress)


class PanelSecurityManager(SecurityManager):
    """SecurityManager с rate-limit на логин (IP + пользователь) и аудитом событий."""

    authdbview = PanelAuthDBView
    userdbmodelview = RuUserDBModelView
    userinfoeditview = RuUserInfoEditView
    groupmodelview = RuUserGroupModelView
    rolemodelview = RuRoleModelView
    permissionmodelview = RuPermissionModelView
    viewmenumodelview = RuViewMenuModelView
    permissionviewmodelview = RuPermissionViewModelView
    userstatschartview = RuUserStatsChartView
    registerusermodelview = RuRegisterUserModelView
    resetmypasswordview = RuResetMyPasswordView
    resetpasswordview = RuResetPasswordView

    def register_views(self):
        super().register_views()

        menu = getattr(self.appbuilder, "menu", None)
        if not menu:
            return

        def _rename(item_name: str, label: str) -> None:
            try:
                item = menu.find(item_name)
            except Exception:
                item = None
            if item:
                item.label = label

        _rename("Security", "Администрирование")
        _rename("List Users", "Пользователи")
        _rename("List Roles", "Роли")
        _rename("List Groups", "Группы")
        _rename("User's Statistics", "Статистика пользователей")
        _rename("User Registrations", "Заявки на регистрацию")
        _rename("Base Permissions", "Основные разрешения")
        _rename("Views/Menus", "Представления/меню")
        _rename("Permission on Views/Menus", "Права на представления/меню")

    def auth_user_db(self, username, password):
        ip = _get_client_ip()
        limited, retry_after = _is_rate_limited(ip, username)
        if limited:
            _set_rate_limit_context(retry_after)
            try:
                log = AuditLog(
                    user=username,
                    action="login",
                    target="web",
                    result="blocked",
                    source="web",
                    ip=ip,
                    details=f"rate_limited:{retry_after}",
                )
                db.session.add(log)
                db.session.commit()
            except Exception:
                db.session.rollback()
            return None

        user = super().auth_user_db(username, password)
        success = user is not None
        if success:
            _clear_login_failures(ip, username)
        else:
            lock_for = _register_failed_attempt(ip, username)
            if lock_for:
                _set_rate_limit_context(lock_for)

        try:
            log = AuditLog(
                user=username,
                action="login",
                target="web",
                result="ok" if success else "fail",
                source="web",
                ip=ip,
                details="ok" if success else "failed",
            )
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()

        return user

