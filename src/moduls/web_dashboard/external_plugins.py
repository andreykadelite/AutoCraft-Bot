from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .utils import ensure_dir

INSTALL_STATE_NOT_APPLICABLE = "not_applicable"
INSTALL_STATE_NOT_INSTALLED = "not_installed"
INSTALL_STATE_INSTALLED = "installed"
INSTALL_STATE_ERROR = "error"

_PYTHON_OVERRIDE_ENV = "AUTOCRAFT_EXTERNAL_PLUGIN_PYTHON"

_PYTHON_SOURCE_LABELS = {
    "env": "Переменная окружения",
    "project_python": "Папка python проекта",
    "current_python": "Текущий Python",
    "missing": "Python не найден",
}


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return Path(str(path))


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = _safe_resolve(path)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def resolve_external_plugin_roots(base_dir: str) -> list[Path]:
    roots: list[Path] = []
    base_path = _safe_resolve(Path(base_dir))
    roots.append(base_path / "data" / "web_plugins")

    try:
        exe_dir = _safe_resolve(Path(sys.executable).parent)
        roots.append(exe_dir / "data" / "web_plugins")
    except Exception:
        pass

    try:
        argv_dir = _safe_resolve(Path(sys.argv[0]).parent)
        roots.append(argv_dir / "data" / "web_plugins")
    except Exception:
        pass

    roots.append(_safe_resolve(Path.cwd()) / "data" / "web_plugins")
    return _unique_paths(roots)


def _project_python_candidates(root: Path) -> list[Path]:
    if os.name == "nt":
        return [
            root / "python" / "python.exe",
            root / "python.exe",
        ]
    return [
        root / "python" / "bin" / "python",
        root / "python" / "python",
        root / "python",
    ]


def resolve_base_python_executable(base_dir: str) -> dict[str, str]:
    override = str(os.environ.get(_PYTHON_OVERRIDE_ENV, "") or "").strip()
    if override:
        override_path = _safe_resolve(Path(override))
        if override_path.is_file():
            return {
                "path": str(override_path),
                "source": "env",
                "source_label": _PYTHON_SOURCE_LABELS["env"],
            }

    roots: list[Path] = [_safe_resolve(Path(base_dir))]
    try:
        roots.append(_safe_resolve(Path(sys.executable).parent))
    except Exception:
        pass
    try:
        roots.append(_safe_resolve(Path(sys.argv[0]).parent))
    except Exception:
        pass
    roots.append(_safe_resolve(Path.cwd()))

    for root in _unique_paths(roots):
        for candidate in _project_python_candidates(root):
            candidate_path = _safe_resolve(candidate)
            if candidate_path.is_file():
                return {
                    "path": str(candidate_path),
                    "source": "project_python",
                    "source_label": _PYTHON_SOURCE_LABELS["project_python"],
                }

    current_python = _safe_resolve(Path(sys.executable))
    current_name = current_python.name.lower()
    if current_python.is_file() and current_name in ("python.exe", "pythonw.exe", "python", "python3"):
        return {
            "path": str(current_python),
            "source": "current_python",
            "source_label": _PYTHON_SOURCE_LABELS["current_python"],
        }

    return {
        "path": "",
        "source": "missing",
        "source_label": _PYTHON_SOURCE_LABELS["missing"],
    }


def _split_dependencies(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    items: list[str] = []
    if isinstance(raw_value, str):
        text = raw_value.replace("\r", "\n")
        for chunk in text.split("\n"):
            for value in chunk.split(","):
                dep = str(value or "").strip()
                if dep:
                    items.append(dep)
    elif isinstance(raw_value, (list, tuple, set)):
        for value in raw_value:
            dep = str(value or "").strip()
            if dep:
                items.append(dep)

    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def parse_manifest_dependencies(manifest: dict[str, Any] | None) -> list[str]:
    manifest = manifest or {}
    for key in ("dependencies", "pip", "packages"):
        deps = _split_dependencies(manifest.get(key))
        if deps:
            return deps
    return []


def resolve_requirements_path(plugin_dir: Path, manifest: dict[str, Any] | None) -> Path | None:
    manifest = manifest or {}
    raw_value = manifest.get("requirements") or manifest.get("requirements_file") or ""
    if raw_value:
        candidate = _safe_resolve(plugin_dir / str(raw_value).strip())
        if candidate.is_file():
            return candidate
        return None

    default_path = _safe_resolve(plugin_dir / "requirements.txt")
    if default_path.is_file():
        return default_path
    return None


def _guess_venv_site_packages(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Lib" / "site-packages"
    lib_dir = venv_dir / "lib"
    if lib_dir.is_dir():
        for candidate in sorted(lib_dir.glob("python*/site-packages")):
            if candidate.is_dir():
                return candidate
    return lib_dir / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def get_external_plugin_venv_paths(plugin_dir: Path) -> dict[str, Path]:
    venv_dir = _safe_resolve(plugin_dir / "venv")
    if os.name == "nt":
        python_exe = venv_dir / "Scripts" / "python.exe"
        pip_exe = venv_dir / "Scripts" / "pip.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        pip_exe = venv_dir / "bin" / "pip"

    return {
        "venv_dir": venv_dir,
        "python_exe": python_exe,
        "pip_exe": pip_exe,
        "site_packages": _guess_venv_site_packages(venv_dir),
    }


def collect_external_plugin_installation_info(
    base_dir: str,
    plugin_dir: Path,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin_dir = _safe_resolve(plugin_dir)
    python_info = resolve_base_python_executable(base_dir)
    deps = parse_manifest_dependencies(manifest)
    requirements_path = resolve_requirements_path(plugin_dir, manifest)
    venv_paths = get_external_plugin_venv_paths(plugin_dir)

    venv_dir = venv_paths["venv_dir"]
    python_exe = venv_paths["python_exe"]
    pip_exe = venv_paths["pip_exe"]
    site_packages = venv_paths["site_packages"]
    marker = venv_dir / "pyvenv.cfg"

    installed = marker.is_file() and python_exe.is_file() and pip_exe.is_file() and site_packages.is_dir()
    broken = venv_dir.exists() and not installed

    if installed:
        state = INSTALL_STATE_INSTALLED
        message = "Среда внешнего расширения готова."
        chip = "ok"
    elif broken:
        state = INSTALL_STATE_ERROR
        message = "Среда расширения повреждена или создана не полностью."
        chip = "bad"
    elif python_info["path"]:
        state = INSTALL_STATE_NOT_INSTALLED
        message = "Среда ещё не создана."
        chip = "warn"
    else:
        state = INSTALL_STATE_ERROR
        message = "Не найден Python для установки внешних расширений."
        chip = "bad"

    return {
        "state": state,
        "message": message,
        "chip": chip,
        "installed": installed,
        "broken": broken,
        "base_python": python_info["path"],
        "base_python_source": python_info["source"],
        "base_python_source_label": python_info["source_label"],
        "dependencies": deps,
        "requirements_path": str(requirements_path) if requirements_path else "",
        "requirements_present": bool(requirements_path and requirements_path.is_file()),
        "venv_dir": str(venv_dir),
        "venv_python": str(python_exe),
        "venv_pip": str(pip_exe),
        "venv_site_packages": str(site_packages),
    }


def _trim_command_output(text: str, limit: int = 1200) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def _run_command(args: list[str], cwd: Path, timeout_seconds: int) -> tuple[int, list[str]]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout_seconds,
    )
    details = [f"$ {' '.join(args)}", f"Код завершения: {proc.returncode}"]
    stdout = _trim_command_output(proc.stdout)
    stderr = _trim_command_output(proc.stderr)
    if stdout:
        details.append(stdout)
    if stderr:
        details.append(stderr)
    return proc.returncode, details


def ensure_external_plugin_environment(
    base_dir: str,
    plugin_dir: Path,
    manifest: dict[str, Any] | None = None,
    *,
    recreate: bool = False,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    plugin_dir = _safe_resolve(plugin_dir)
    ensure_dir(plugin_dir)
    before = collect_external_plugin_installation_info(base_dir, plugin_dir, manifest)
    details: list[str] = []

    base_python = str(before.get("base_python") or "").strip()
    if not base_python:
        return {
            "ok": False,
            "message": "Не удалось найти Python в папке python для установки внешнего расширения.",
            "details": details,
            "state": before,
        }

    venv_dir = Path(str(before["venv_dir"]))
    if recreate and venv_dir.exists():
        try:
            shutil.rmtree(venv_dir)
            details.append(f"Удалено старое окружение: {venv_dir}")
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Не удалось удалить старое окружение: {exc}",
                "details": details,
                "state": before,
            }

    if not venv_dir.exists():
        code, output = _run_command([base_python, "-m", "venv", str(venv_dir)], plugin_dir, timeout_seconds)
        details.extend(output)
        if code != 0:
            return {
                "ok": False,
                "message": "Не удалось создать виртуальное окружение расширения.",
                "details": details,
                "state": before,
            }

    after_create = collect_external_plugin_installation_info(base_dir, plugin_dir, manifest)
    venv_python = str(after_create.get("venv_python") or "").strip()
    if not venv_python:
        return {
            "ok": False,
            "message": "После создания окружения не найден python.exe внутри venv.",
            "details": details,
            "state": after_create,
        }

    code, output = _run_command(
        [venv_python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        plugin_dir,
        timeout_seconds,
    )
    details.extend(output)
    if code != 0:
        return {
            "ok": False,
            "message": "Не удалось обновить pip/setuptools/wheel для расширения.",
            "details": details,
            "state": after_create,
        }

    requirements_path = str(after_create.get("requirements_path") or "").strip()
    if requirements_path:
        code, output = _run_command(
            [venv_python, "-m", "pip", "install", "--upgrade", "-r", requirements_path],
            plugin_dir,
            timeout_seconds,
        )
        details.extend(output)
        if code != 0:
            return {
                "ok": False,
                "message": "Не удалось установить зависимости из requirements.txt.",
                "details": details,
                "state": after_create,
            }

    dependencies = list(after_create.get("dependencies") or [])
    if dependencies:
        code, output = _run_command(
            [venv_python, "-m", "pip", "install", "--upgrade", *dependencies],
            plugin_dir,
            timeout_seconds,
        )
        details.extend(output)
        if code != 0:
            return {
                "ok": False,
                "message": "Не удалось установить зависимости расширения.",
                "details": details,
                "state": after_create,
            }

    final_state = collect_external_plugin_installation_info(base_dir, plugin_dir, manifest)
    if not final_state.get("installed"):
        return {
            "ok": False,
            "message": "Окружение создано, но не прошло итоговую проверку.",
            "details": details,
            "state": final_state,
        }

    dependencies_text = ", ".join(dependencies) if dependencies else "без дополнительных pip-зависимостей"
    action = "переустановлена" if recreate else "установлена"
    return {
        "ok": True,
        "message": f"Среда внешнего расширения {action}. Зависимости: {dependencies_text}.",
        "details": details,
        "state": final_state,
    }
