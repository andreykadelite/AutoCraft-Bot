from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover - platform specific
    winreg = None  # type: ignore


class RegistryError(RuntimeError):
    pass


_ROOT_ALIASES = {
    "HKLM": "HKLM",
    "HKEY_LOCAL_MACHINE": "HKLM",
    "HKCU": "HKCU",
    "HKEY_CURRENT_USER": "HKCU",
    "HKCR": "HKCR",
    "HKEY_CLASSES_ROOT": "HKCR",
    "HKU": "HKU",
    "HKEY_USERS": "HKU",
    "HKCC": "HKCC",
    "HKEY_CURRENT_CONFIG": "HKCC",
}

_ROOTS: Dict[str, Dict[str, Any]] = {}
if winreg:
    _ROOTS = {
        "HKLM": {"const": winreg.HKEY_LOCAL_MACHINE, "label": "HKEY_LOCAL_MACHINE"},
        "HKCU": {"const": winreg.HKEY_CURRENT_USER, "label": "HKEY_CURRENT_USER"},
        "HKCR": {"const": winreg.HKEY_CLASSES_ROOT, "label": "HKEY_CLASSES_ROOT"},
        "HKU": {"const": winreg.HKEY_USERS, "label": "HKEY_USERS"},
        "HKCC": {"const": winreg.HKEY_CURRENT_CONFIG, "label": "HKEY_CURRENT_CONFIG"},
    }

_ROOTS_UI = [
    {"value": "HKLM", "label": "HKLM (HKEY_LOCAL_MACHINE)"},
    {"value": "HKCU", "label": "HKCU (HKEY_CURRENT_USER)"},
    {"value": "HKCR", "label": "HKCR (HKEY_CLASSES_ROOT)"},
    {"value": "HKU", "label": "HKU (HKEY_USERS)"},
    {"value": "HKCC", "label": "HKCC (HKEY_CURRENT_CONFIG)"},
]

_SUPPORTED_TYPES = [
    {"value": "REG_SZ", "label": "Строка (REG_SZ)"},
    {"value": "REG_EXPAND_SZ", "label": "Расширяемая строка (REG_EXPAND_SZ)"},
    {"value": "REG_MULTI_SZ", "label": "Многострочная (REG_MULTI_SZ)"},
    {"value": "REG_DWORD", "label": "DWORD 32-бит (REG_DWORD)"},
    {"value": "REG_QWORD", "label": "QWORD 64-бит (REG_QWORD)"},
    {"value": "REG_BINARY", "label": "Бинарные данные (REG_BINARY)"},
    {"value": "REG_NONE", "label": "Без типа (REG_NONE)"},
]

_TYPE_NAME_TO_CONST: Dict[str, int] = {}
_TYPE_CONST_TO_NAME: Dict[int, str] = {}
if winreg:
    _TYPE_NAME_TO_CONST = {
        "REG_NONE": winreg.REG_NONE,
        "REG_SZ": winreg.REG_SZ,
        "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
        "REG_BINARY": winreg.REG_BINARY,
        "REG_DWORD": winreg.REG_DWORD,
        "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
        "REG_QWORD": winreg.REG_QWORD,
    }
    _TYPE_CONST_TO_NAME = {v: k for k, v in _TYPE_NAME_TO_CONST.items()}


_MAX_PREVIEW_CHARS = 200
_MAX_RAW_CHARS = 4096


def registry_available() -> Tuple[bool, str]:
    if os.name != "nt" or winreg is None:
        return False, "Редактор реестра доступен только на Windows."
    return True, ""


def default_registry_view() -> str:
    arch = platform.machine().lower()
    if "64" in arch:
        return "64"
    return "default"


def get_roots_ui() -> List[Dict[str, str]]:
    return list(_ROOTS_UI)


def get_supported_types() -> List[Dict[str, str]]:
    return list(_SUPPORTED_TYPES)


def normalize_registry_path(path: str) -> str:
    root_const, subkey, root_short = _split_path(path)
    return _format_path(root_short, subkey)


def list_key(path: str, view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, root_short = _split_path(path)
    path = _format_path(root_short, subkey)
    subkeys: List[Dict[str, Any]] = []
    values: List[Dict[str, Any]] = []

    with _open_key(root_const, subkey, _read_access(view), view) as key:
        subkey_count, value_count, _ = winreg.QueryInfoKey(key)
        for idx in range(subkey_count):
            try:
                name = winreg.EnumKey(key, idx)
            except OSError:
                break
            child_subkey = f"{subkey}\\{name}" if subkey else name
            child_path = _format_path(root_short, child_subkey)
            subkeys.append(
                {
                    "name": name,
                    "path": child_path,
                    "has_children": _has_children(root_const, child_subkey, view),
                }
            )

        for idx in range(value_count):
            try:
                name, data, vtype = winreg.EnumValue(key, idx)
            except OSError:
                break
            values.append(_format_value(name, data, vtype))

    subkeys.sort(key=lambda item: item["name"].lower())
    values.sort(key=lambda item: (0 if item["is_default"] else 1, item["name"].lower()))
    return {"path": path, "subkeys": subkeys, "values": values}


def get_value(path: str, name: str = "", view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    with _open_key(root_const, subkey, _read_access(view), view) as key:
        try:
            data, vtype = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            raise RegistryError("Значение не найдено.")
    return _format_value(name, data, vtype, include_raw=True)


def search_registry(
    path: str,
    query: str,
    view: str = "default",
    max_depth: int = 4,
    max_results: int = 200,
    search_keys: bool = True,
    search_values: bool = True,
    search_data: bool = False,
    timeout_seconds: float = 3.0,
) -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, root_short = _split_path(path)
    query = (query or "").strip()
    if not query:
        raise RegistryError("Введите текст для поиска.")

    query_lower = query.lower()
    start_time = time.monotonic()
    results: List[Dict[str, Any]] = []
    truncated = False

    queue: List[Tuple[str, int]] = [(subkey, 0)]
    visited = 0

    while queue:
        current_subkey, depth = queue.pop(0)
        visited += 1
        if visited > 5000:
            truncated = True
            break
        if time.monotonic() - start_time > timeout_seconds:
            truncated = True
            break

        current_path = _format_path(root_short, current_subkey)
        try:
            with _open_key(root_const, current_subkey, _read_access(view), view) as key:
                key_name = current_subkey.split("\\")[-1] if current_subkey else root_short
                if search_keys and query_lower in key_name.lower():
                    results.append({"kind": "key", "path": current_path, "name": key_name})
                    if len(results) >= max_results:
                        truncated = True
                        break

                if search_values:
                    value_count = winreg.QueryInfoKey(key)[1]
                    for idx in range(value_count):
                        try:
                            name, data, vtype = winreg.EnumValue(key, idx)
                        except OSError:
                            break
                        name_display = name or "(По умолчанию)"
                        if query_lower in name_display.lower():
                            results.append(
                                {
                                    "kind": "value",
                                    "path": current_path,
                                    "name": name,
                                    "display_name": name_display,
                                    "type": _TYPE_CONST_TO_NAME.get(vtype, f"REG_{vtype}"),
                                }
                            )
                        elif search_data:
                            data_text = _value_data_text(data, vtype)
                            if query_lower in data_text.lower():
                                results.append(
                                    {
                                        "kind": "value",
                                        "path": current_path,
                                        "name": name,
                                        "display_name": name_display,
                                        "type": _TYPE_CONST_TO_NAME.get(vtype, f"REG_{vtype}"),
                                    }
                                )
                        if len(results) >= max_results:
                            truncated = True
                            break

                if depth < max_depth:
                    subkey_count = winreg.QueryInfoKey(key)[0]
                    for idx in range(subkey_count):
                        try:
                            name = winreg.EnumKey(key, idx)
                        except OSError:
                            break
                        next_subkey = f"{current_subkey}\\{name}" if current_subkey else name
                        queue.append((next_subkey, depth + 1))
        except RegistryError:
            continue

    return {"path": normalize_registry_path(path), "query": query, "results": results, "truncated": truncated}


def registry_create_key(path: str, view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    if not subkey:
        raise RegistryError("Нельзя создать корневой ключ.")
    if _key_exists(root_const, subkey, view):
        raise RegistryError("Ключ уже существует.")

    parent_subkey, key_name = _split_parent(subkey)
    if not key_name:
        raise RegistryError("Введите имя ключа.")
    if "\\" in key_name:
        raise RegistryError("Имя ключа не должно содержать \\.")

    with _open_key(root_const, parent_subkey, _write_access(view), view) as parent:
        winreg.CreateKeyEx(parent, key_name, 0, _write_access(view))
    return {"ok": True, "stdout": "Ключ создан."}


def registry_delete_key(path: str, recursive: bool = True, view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    if not subkey:
        raise RegistryError("Нельзя удалить корневой ключ.")
    if recursive:
        _delete_tree(root_const, subkey, view)
    else:
        _delete_single(root_const, subkey, view)
    return {"ok": True, "stdout": "Ключ удален."}


def registry_rename_key(path: str, new_name: str, view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    if not subkey:
        raise RegistryError("Нельзя переименовать корневой ключ.")
    new_name = (new_name or "").strip()
    if not new_name:
        raise RegistryError("Введите новое имя ключа.")
    if "\\" in new_name:
        raise RegistryError("Имя ключа не должно содержать \\.")

    parent_subkey, old_name = _split_parent(subkey)
    target_subkey = f"{parent_subkey}\\{new_name}" if parent_subkey else new_name
    if _key_exists(root_const, target_subkey, view):
        raise RegistryError("Ключ с таким именем уже существует.")

    if hasattr(winreg, "RenameKey"):
        with _open_key(root_const, parent_subkey, _write_access(view), view) as parent:
            winreg.RenameKey(parent, old_name, new_name)
    else:
        _copy_tree(root_const, subkey, root_const, target_subkey, view)
        _delete_tree(root_const, subkey, view)

    return {"ok": True, "stdout": "Ключ переименован."}


def registry_set_value(
    path: str,
    name: str,
    value_type: str | int,
    data: Any,
    view: str = "default",
) -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    if not subkey and root_const is None:
        raise RegistryError("Укажите путь.")

    value_type_const = _resolve_value_type(value_type)
    parsed = _parse_value_data(value_type_const, data)

    with _open_key(root_const, subkey, _write_access(view), view) as key:
        winreg.SetValueEx(key, name or "", 0, value_type_const, parsed)
    return {"ok": True, "stdout": "Значение сохранено."}


def registry_delete_value(path: str, name: str, view: str = "default") -> Dict[str, Any]:
    _ensure_available()
    root_const, subkey, _ = _split_path(path)
    with _open_key(root_const, subkey, _write_access(view), view) as key:
        try:
            winreg.DeleteValue(key, name or "")
        except FileNotFoundError:
            raise RegistryError("Значение не найдено.")
    return {"ok": True, "stdout": "Значение удалено."}


def registry_import(file_path: str) -> Dict[str, Any]:
    _ensure_available()
    if not file_path:
        raise RegistryError("Файл не указан.")
    path = Path(file_path)
    if not path.exists():
        raise RegistryError("Файл импорта не найден.")
    if path.suffix.lower() != ".reg":
        raise RegistryError("Поддерживаются только .reg файлы.")

    try:
        _run_reg_command(["reg", "import", str(path)])
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    return {"ok": True, "stdout": "Импорт завершен."}


def build_registry_export(
    path: str,
    view: str = "default",
    export_all: bool = False,
    suggested_name: str | None = None,
) -> Tuple[Path, str]:
    _ensure_available()
    temp_dir = Path(tempfile.gettempdir()) / "autocraft_registry" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    if export_all:
        filename = _safe_filename(
            suggested_name or f"registry_full_export_{time.strftime('%Y%m%d_%H%M%S')}.zip",
            ".zip",
        )
        zip_path = temp_dir / filename
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root in _ROOTS.keys():
                export_file = temp_dir / f"{root}.reg"
                _run_reg_command(_build_reg_export_cmd(root, export_file, view))
                zf.write(export_file, export_file.name)
        return zip_path, filename

    if not path:
        raise RegistryError("Укажите путь ключа.")
    _root_const, subkey, root_short = _split_path(path)
    full_path = _format_path(root_short, subkey)
    base_name = suggested_name or f"registry_{root_short}_{subkey or 'root'}"
    filename = _safe_filename(base_name, ".reg")
    export_file = temp_dir / filename
    _run_reg_command(_build_reg_export_cmd(full_path, export_file, view))
    return export_file, filename


def build_registry_full_export_with_progress(
    view: str = "default",
    suggested_name: str | None = None,
    progress_cb: Any | None = None,
) -> Tuple[Path, str]:
    _ensure_available()
    temp_dir = Path(tempfile.gettempdir()) / "autocraft_registry" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(
        suggested_name or f"registry_full_export_{time.strftime('%Y%m%d_%H%M%S')}.zip",
        ".zip",
    )
    zip_path = temp_dir / filename
    roots = list(_ROOTS.keys())
    total = max(len(roots), 1)
    completed = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root in roots:
            export_file = temp_dir / f"{root}.reg"
            _run_reg_command(_build_reg_export_cmd(root, export_file, view))
            zf.write(export_file, export_file.name)
            completed += 1
            if callable(progress_cb):
                try:
                    progress_cb(completed / total)
                except Exception:
                    pass
    return zip_path, filename


def cleanup_export_file(path: Path) -> None:
    try:
        parent = path.parent
        if path.exists():
            path.unlink()
        if parent.exists():
            for item in parent.glob("*"):
                try:
                    item.unlink()
                except Exception:
                    pass
            parent.rmdir()
    except Exception:
        pass


def _build_reg_export_cmd(path: str, target: Path, view: str) -> List[str]:
    cmd = ["reg", "export", path, str(target), "/y"]
    view_flag = _reg_view_flag(view)
    if view_flag:
        cmd.append(view_flag)
    return cmd


def _reg_view_flag(view: str) -> str:
    value = (view or "").strip().lower()
    if value in {"32", "wow64_32", "x86", "32bit"}:
        return "/reg:32"
    if value in {"64", "wow64_64", "x64", "64bit"}:
        return "/reg:64"
    return ""


def _run_reg_command(args: List[str]) -> None:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        raise RegistryError(f"Не удалось выполнить команду: {exc}")

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RegistryError(stderr or "Команда reg.exe завершилась с ошибкой.")


def _ensure_available() -> None:
    ok, message = registry_available()
    if not ok:
        raise RegistryError(message)


def _normalize_root(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value.endswith(":"):
        value = value[:-1]
    return _ROOT_ALIASES.get(value, value)


def _split_path(path: str) -> Tuple[Any, str, str]:
    if not path:
        raise RegistryError("Укажите путь ключа.")
    raw = (path or "").strip().replace("/", "\\").strip("\\")
    if not raw:
        raise RegistryError("Укажите путь ключа.")
    parts = raw.split("\\")
    root_label = _normalize_root(parts[0])
    if root_label not in _ROOTS:
        raise RegistryError("Неизвестный корневой раздел реестра.")
    root_const = _ROOTS[root_label]["const"]
    subkey = "\\".join(parts[1:]).strip()
    return root_const, subkey, root_label


def _format_path(root_label: str, subkey: str) -> str:
    return f"{root_label}\\{subkey}" if subkey else root_label


def _read_access(view: str) -> int:
    return winreg.KEY_READ | _view_flag(view)


def _write_access(view: str) -> int:
    return winreg.KEY_READ | winreg.KEY_WRITE | _view_flag(view)


def _view_flag(view: str) -> int:
    if not winreg:
        return 0
    value = (view or "").strip().lower()
    if value in {"32", "wow64_32", "x86", "32bit"}:
        return getattr(winreg, "KEY_WOW64_32KEY", 0)
    if value in {"64", "wow64_64", "x64", "64bit"}:
        return getattr(winreg, "KEY_WOW64_64KEY", 0)
    return 0


def _open_key(root_const: Any, subkey: str, access: int, view: str):
    try:
        return winreg.OpenKey(root_const, subkey or "", 0, access)
    except FileNotFoundError:
        raise RegistryError("Ключ не найден.")
    except PermissionError:
        raise RegistryError("Нет доступа к ключу.")
    except OSError as exc:
        raise RegistryError(str(exc))


def _has_children(root_const: Any, subkey: str, view: str) -> bool:
    try:
        with _open_key(root_const, subkey, _read_access(view), view) as key:
            subkey_count = winreg.QueryInfoKey(key)[0]
            return subkey_count > 0
    except RegistryError:
        return False


def _key_exists(root_const: Any, subkey: str, view: str) -> bool:
    try:
        with _open_key(root_const, subkey, _read_access(view), view):
            return True
    except RegistryError as exc:
        if "не найден" in str(exc).lower():
            return False
        raise


def _split_parent(subkey: str) -> Tuple[str, str]:
    if "\\" in subkey:
        parent, name = subkey.rsplit("\\", 1)
        return parent, name
    return "", subkey


def _delete_single(root_const: Any, subkey: str, view: str) -> None:
    try:
        if hasattr(winreg, "DeleteKeyEx"):
            winreg.DeleteKeyEx(root_const, subkey, _view_flag(view), 0)
        else:
            winreg.DeleteKey(root_const, subkey)
    except FileNotFoundError:
        raise RegistryError("Ключ не найден.")
    except PermissionError:
        raise RegistryError("Нет доступа к ключу.")
    except OSError as exc:
        raise RegistryError(str(exc))


def _delete_tree(root_const: Any, subkey: str, view: str) -> None:
    with _open_key(root_const, subkey, _read_access(view), view) as key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            child_path = f"{subkey}\\{child}"
            _delete_tree(root_const, child_path, view)
    _delete_single(root_const, subkey, view)


def _copy_tree(src_root: Any, src_subkey: str, dst_root: Any, dst_subkey: str, view: str) -> None:
    with _open_key(src_root, src_subkey, _read_access(view), view) as src_key:
        dst_key = winreg.CreateKeyEx(dst_root, dst_subkey, 0, _write_access(view))
        with dst_key:
            subkey_count, value_count, _ = winreg.QueryInfoKey(src_key)
            for idx in range(value_count):
                name, data, vtype = winreg.EnumValue(src_key, idx)
                winreg.SetValueEx(dst_key, name, 0, vtype, data)
            for idx in range(subkey_count):
                name = winreg.EnumKey(src_key, idx)
                child_src = f"{src_subkey}\\{name}"
                child_dst = f"{dst_subkey}\\{name}"
                _copy_tree(src_root, child_src, dst_root, child_dst, view)


def _format_value(name: str, data: Any, vtype: int, include_raw: bool = False) -> Dict[str, Any]:
    type_name = _TYPE_CONST_TO_NAME.get(vtype, f"REG_{vtype}")
    raw_text = _value_data_text(data, vtype)
    preview, preview_truncated = _clip_text(raw_text, _MAX_PREVIEW_CHARS)
    raw, raw_truncated = _clip_text(raw_text, _MAX_RAW_CHARS)
    is_default = name == ""
    return {
        "name": name,
        "display_name": "(По умолчанию)" if is_default else name,
        "type": type_name,
        "type_code": vtype,
        "data_preview": preview,
        "data_raw": raw if include_raw or not raw_truncated else "",
        "data_truncated": raw_truncated,
        "preview_truncated": preview_truncated,
        "is_default": is_default,
        "editable": type_name in _TYPE_NAME_TO_CONST,
    }


def _value_data_text(data: Any, vtype: int) -> str:
    try:
        if vtype in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            return str(data or "")
        if vtype == winreg.REG_MULTI_SZ:
            if isinstance(data, (list, tuple)):
                return "\n".join(str(item) for item in data)
            return str(data or "")
        if vtype == winreg.REG_DWORD:
            number = int(data or 0)
            return f"0x{number:08X} ({number})"
        if vtype == winreg.REG_QWORD:
            number = int(data or 0)
            return f"0x{number:016X} ({number})"
        if vtype in (winreg.REG_BINARY, winreg.REG_NONE):
            return _bytes_to_hex(data)
    except Exception:
        pass
    return str(data or "")


def _bytes_to_hex(data: Any) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    try:
        return " ".join(f"{b:02X}" for b in bytes(data))
    except Exception:
        return str(data)


def _clip_text(text: str, limit: int) -> Tuple[str, bool]:
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…", True


def _resolve_value_type(value_type: str | int) -> int:
    if isinstance(value_type, int):
        return value_type
    name = (value_type or "").strip().upper()
    if name not in _TYPE_NAME_TO_CONST:
        raise RegistryError("Неизвестный тип значения.")
    return _TYPE_NAME_TO_CONST[name]


def _parse_value_data(value_type: int, data: Any) -> Any:
    if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return str(data or "")
    if value_type == winreg.REG_MULTI_SZ:
        if isinstance(data, (list, tuple)):
            return [str(item) for item in data]
        lines = str(data or "").splitlines()
        return [line for line in lines]
    if value_type == winreg.REG_DWORD:
        return _parse_int(data, 32)
    if value_type == winreg.REG_QWORD:
        return _parse_int(data, 64)
    if value_type in (winreg.REG_BINARY, winreg.REG_NONE):
        return _parse_hex(data)
    raise RegistryError("Тип значения не поддерживается для записи.")


def _parse_int(value: Any, bits: int) -> int:
    if isinstance(value, int):
        number = value
    else:
        text = str(value or "").strip().lower()
        if not text:
            raise RegistryError("Введите число.")
        base = 16 if text.startswith("0x") else 10
        if text.startswith("0x"):
            text = text[2:]
        number = int(text, base)
    max_value = (1 << bits) - 1
    if number < 0 or number > max_value:
        raise RegistryError("Число выходит за пределы допустимого диапазона.")
    return number


def _parse_hex(value: Any) -> bytes:
    if value is None:
        return b""
    text = str(value).strip().lower()
    if not text:
        return b""
    if text.startswith("0x"):
        text = text[2:]
    text = re.sub(r"[^0-9a-f]", "", text)
    if len(text) % 2 != 0:
        raise RegistryError("Бинарные данные должны содержать четное число символов.")
    return bytes.fromhex(text)


def _safe_filename(value: str, suffix: str) -> str:
    raw = (value or "").strip()
    if raw.lower().endswith(suffix.lower()):
        raw = raw[: -len(suffix)]
    raw = raw.replace(" ", "_")
    raw = re.sub(r"[^a-zA-Z0-9._-]", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("._-")
    if not raw:
        raw = "registry_export"
    raw = raw[:120]
    return raw + suffix
