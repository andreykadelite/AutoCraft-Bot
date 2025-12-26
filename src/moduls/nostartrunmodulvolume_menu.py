from aiogram import types, Dispatcher
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keymenu import get_main_keyboard, get_additional_keyboard

# Позволяет "пропускать" сообщение дальше к другим хендлерам, если это не наша ситуация
try:
    from aiogram.dispatcher.handler import SkipHandler
except Exception:
    SkipHandler = None

from pathlib import Path
import configparser
import subprocess
import sys
import logging

# Dependencies specifically for volume control
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import POINTER, cast
from comtypes import CLSCTX_ALL
import comtypes


# ================== ЛОГИРОВАНИЕ ==================

def _init_logger():
    """
    Инициализация логирования модуля.

    Логирование в файл включается только если в config.ini
    (секция [credentials]) установлен debug = true.
    Файл лога создаётся в папке "log" внутри base_dir (как в основном боте).
    """
    logger = logging.getLogger(__name__)

    # Если логгер уже настроен выше (например, общим конфигом logging),
    # не трогаем его конфигурацию.
    if logger.handlers:
        return logger, None

    # Определяем base_dir максимально совместимо с bot-ok.py
    base_dir = None
    config_path = None

    # 1) Пытаемся взять base_dir / CONFIG_FILE из основного модуля (bot-ok.py)
    try:
        import __main__ as main
        base_dir = getattr(main, "base_dir", None)
        config_path = getattr(main, "CONFIG_FILE", None)
    except Exception:
        pass

    # 2) Если не получилось — определяем base_dir самостоятельно
    if base_dir is None:
        try:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).resolve().parent
            else:
                base_dir = Path(__file__).resolve().parent
        except Exception:
            base_dir = Path.cwd()
    else:
        base_dir = Path(base_dir)

    # Путь к config.ini
    if config_path is None:
        config_path = base_dir / "config.ini"
    else:
        config_path = Path(config_path)

    # Читаем флаг debug из config.ini
    debug_enabled = False
    try:
        cfg = configparser.ConfigParser()
        cfg.read(config_path, encoding="utf-8")
        debug_enabled = cfg.getboolean("credentials", "debug", fallback=False)
    except Exception:
        debug_enabled = False

    log_file = None

    if debug_enabled:
        try:
            log_dir = base_dir / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            # Имя файла лога по имени модуля, чтобы было понятно, что это громкость
            log_file = log_dir / f"{Path(__file__).stem}.log"

            handler = logging.FileHandler(log_file, encoding="utf-8")
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            )
            handler.setFormatter(formatter)

            logger.setLevel(logging.DEBUG)
            logger.addHandler(handler)
            logger.propagate = True

            logger.debug(
                "Логирование %s инициализировано. Лог-файл: %s (debug=%s)",
                __name__,
                log_file,
                debug_enabled,
            )
        except Exception:
            # В случае проблем с файлом не роняем модуль
            log_file = None

    # Если debug выключен и хендлеров так и не появилось — вешаем NullHandler,
    # чтобы избежать предупреждений от logging, но при этом позволить
    # сообщениям уходить в корневой логгер.
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    return logger, log_file


try:
    logger, LOG_FILE = _init_logger()
except Exception:
    # Фолбэк, если вообще что-то пошло не так при инициализации логов
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    LOG_FILE = None


# ================== ВНУТРЕННЕЕ СОСТОЯНИЕ МОДУЛЯ ==================
# Выбор устройства воспроизведения (только состояние диалога выбора)
AUDIO_OUTPUT_STATE = {}   # {chat_id: {"state": "select_output", "devices": [ {"name": str, "id": str}, ... ]}}


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def _get_nircmd_path():
    """
    Возвращает путь к nircmd.exe.

    ⚠️ ВНИМАНИЕ: в текущей версии модуля NirCmd НЕ используется
    для смены устройства вывода, всё делается через системный
    COM-интерфейс Windows (IPolicyConfig). Функция оставлена как
    запасной вариант/для совместимости, но нигде не вызывается.
    """
    logger.debug("_get_nircmd_path(): поиск nircmd.exe")
    candidates = []

    # 1) Папка рядом с exe (актуально для Nuitka onefile/temp)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "nircmd-x64" / "nircmd.exe")
    except Exception as e:
        logger.debug("_get_nircmd_path(): ошибка определения exe_dir: %s", e)
        exe_dir = None

    # 2) Папка рядом с самим модулем (режим разработки)
    try:
        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir / "nircmd-x64" / "nircmd.exe")
    except Exception as e:
        logger.debug("_get_nircmd_path(): ошибка определения script_dir: %s", e)
        script_dir = None

    # 3) Папка по sys.argv[0] (на случай нестандартного запуска)
    try:
        argv_dir = Path(sys.argv[0]).resolve().parent
        if not any(p.parent == argv_dir / "nircmd-x64" for p in candidates):
            candidates.append(argv_dir / "nircmd-x64" / "nircmd.exe")
    except Exception as e:
        logger.debug("_get_nircmd_path(): ошибка определения argv_dir: %s", e)

    for p in candidates:
        try:
            if p.is_file():
                logger.debug("_get_nircmd_path(): найден nircmd.exe по пути %s", p)
                return p
        except Exception as e:
            logger.debug("_get_nircmd_path(): ошибка проверки пути %s: %s", p, e)

    if candidates:
        fallback = candidates[-1]
        logger.warning(
            "_get_nircmd_path(): ни один из кандидатов не существует, возвращаю последний: %s",
            fallback
        )
        return fallback

    logger.warning("_get_nircmd_path(): кандидатов нет, возвращаю 'nircmd.exe'")
    return Path("nircmd.exe")


def _run_nircmd(args):
    """
    Запускает NirCmd с переданными аргументами.
    Возвращает CompletedProcess или None при ошибке/отсутствии файла.

    ⚠️ Сейчас NirCmd не используется для управления аудио, функция
    оставлена только как резерв.
    """
    logger.debug("_run_nircmd(): args=%s", args)
    try:
        nircmd_path = _get_nircmd_path()
        if not nircmd_path.is_file():
            logger.warning("_run_nircmd(): nircmd.exe не найден по пути %s", nircmd_path)
            return None
        completed = subprocess.run(
            [str(nircmd_path), *args],
            capture_output=True,
            text=True
        )
        logger.debug(
            "_run_nircmd(): returncode=%s, stdout=%r, stderr=%r",
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
        return completed
    except Exception as e:
        logger.exception("_run_nircmd(): ошибка запуска NirCmd: %s", e)
        return None


def _set_default_playback_device(device_id: str):
    """
    Устанавливает системное УСТРОЙСТВО ВОСПРОИЗВЕДЕНИЯ по умолчанию
    для всех ролей (Console/Multimedia/Communications) через
    системный COM-интерфейс Windows PolicyConfig (IPolicyConfigVista).

    device_id: строка вида "{0.0.0.00000000}.{GUID}", которую даёт
    MMDevice API / Pycaw (свойство dev.id).

    Возвращает (успех: bool, подробности: str).
    """
    logger.info("Запрос смены устройства воспроизведения по умолчанию: device_id=%r", device_id)

    if not device_id:
        logger.warning("_set_default_playback_device(): пустой идентификатор устройства")
        return False, "Пустой идентификатор устройства (device id)."

    logs = []
    success = False

    try:
        from ctypes import (
            HRESULT,
            c_wchar_p,
            c_int,
            c_longlong,
            Structure,
            POINTER,
        )
        from ctypes.wintypes import DWORD
        from comtypes import GUID, COMMETHOD, IUnknown
        from comtypes.client import CreateObject

        # --- Заглушки для структур, которые нам напрямую не нужны ---
        class WAVEFORMATEX(Structure):
            _fields_ = []

        class DeviceShareMode(Structure):
            _fields_ = []

        class PROPERTYKEY(Structure):
            _fields_ = [
                ("fmtid", GUID),
                ("pid", DWORD),
            ]

        class PROPVARIANT(Structure):
            # Полное описание не нужно — мы работаем только с указателем.
            _fields_ = [
                ("vt", c_int),
                ("wReserved1", c_int),
                ("wReserved2", c_int),
                ("wReserved3", c_int),
                ("data", c_longlong),
            ]

        PINT64 = POINTER(c_longlong)

        class IPolicyConfigVista(IUnknown):
            _iid_ = GUID("{568b9108-44bf-40b4-9006-86afe5b5a620}")
            _methods_ = (
                # vtable строго повторяет PolicyConfig.h
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetMixFormat",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppFormat"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetDeviceFormat",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], c_int, "bDefault"),
                    (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppFormat"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetDeviceFormat",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], POINTER(WAVEFORMATEX), "pEndpointFormat"),
                    (["in"], POINTER(WAVEFORMATEX), "pMixFormat"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetProcessingPeriod",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], c_int, "bDefault"),
                    (["out"], PINT64, "hnsDefaultDevicePeriod"),
                    (["out"], PINT64, "hnsMinimumDevicePeriod"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetProcessingPeriod",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], PINT64, "hnsDevicePeriod"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetShareMode",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["out"], POINTER(DeviceShareMode), "pShareMode"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetShareMode",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], POINTER(DeviceShareMode), "pShareMode"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetPropertyValue",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], POINTER(PROPERTYKEY), "key"),
                    (["out"], POINTER(PROPVARIANT), "pv"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetPropertyValue",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], POINTER(PROPERTYKEY), "key"),
                    (["in"], POINTER(PROPVARIANT), "pv"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetDefaultEndpoint",
                    (["in"], c_wchar_p, "wszDeviceId"),
                    (["in"], DWORD, "eRole"),
                ),
                COMMETHOD(
                    [],
                    HRESULT,
                    "SetEndpointVisibility",
                    (["in"], c_wchar_p, "pszDeviceName"),
                    (["in"], c_int, "bVisible"),
                ),
            )

        CLSID_CPolicyConfigVistaClient = GUID("{294935CE-F637-4E7C-A41B-AB255460B862}")

        # Создаём COM-объект через comtypes.client — он сам позаботится о CoInitialize
        policy_config = CreateObject(CLSID_CPolicyConfigVistaClient, interface=IPolicyConfigVista)

        # 0 = eConsole, 1 = eMultimedia, 2 = eCommunications
        roles = (
            (0, "Console"),
            (1, "Multimedia"),
            (2, "Communications"),
        )

        for role, role_name in roles:
            try:
                hr = policy_config.SetDefaultEndpoint(device_id, DWORD(role))
                hr_int = int(hr)
                msg = f"[{role_name}] SetDefaultEndpoint hr=0x{hr_int:08X}"
                logs.append(msg)
                if hr_int == 0:
                    success = True
                logger.debug("_set_default_playback_device(): %s", msg)
            except Exception as e:
                err_msg = f"[{role_name}] Исключение SetDefaultEndpoint: {e}"
                logs.append(err_msg)
                logger.exception("_set_default_playback_device(): %s", err_msg)

    except Exception as e:
        err = f"Ошибка инициализации PolicyConfig: {e}"
        logs.append(err)
        logger.exception("_set_default_playback_device(): %s", err)

    details = "\n".join(logs) if logs else "Нет логов от PolicyConfig."

    global _VOLUME_IFACE, _VOLUME_DEVICE
    if success:
        logger.info(
            "Устройство по умолчанию успешно переключено на device_id=%r. Сброс кэша громкости.",
            device_id
        )
        _VOLUME_IFACE = None
        _VOLUME_DEVICE = None
    else:
        logger.warning(
            "Не удалось переключить устройство по умолчанию на device_id=%r. Подробности: %s",
            device_id,
            details,
        )

    return success, details


def _get_volume_control_keyboard(is_muted: bool):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Уменьшить громкость"), KeyboardButton("Увеличить громкость"))
    label = "Включить звук" if is_muted else "Выключить звук"
    kb.add(KeyboardButton(label))
    kb.add(KeyboardButton("Сменить устройство воспроизведения"))
    kb.add(KeyboardButton("Вернуться в доп.меню"), KeyboardButton("На главную"))
    return kb


def _get_output_devices_keyboard(devices, default_name):
    """
    Сформировать клавиатуру устройств вывода.

    devices: список словарей вида:
        {"name": <человекочитаемое имя>, "id": <MMDevice ID>, "label": <строка для пользователя>}
    default_name: имя системного устройства по умолчанию (FriendlyName).
    """
    logger.debug(
        "_get_output_devices_keyboard(): устройств=%d, default_name=%r",
        len(devices),
        default_name,
    )
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    for i, dev in enumerate(devices, 1):
        base_name = dev.get("name", "Аудио устройство")
        label = dev.get("label") or base_name
        # помечаем текущее устройство по умолчанию
        if default_name and base_name == default_name:
            label = f"{label} (по умолчанию)"
        kb.add(KeyboardButton(f"{i}. {label}"))
    kb.add(KeyboardButton("Отмена"))
    return kb


def _get_default_playback_device_name():
    """Вернуть имя системного устройства воспроизведения по умолчанию.

    Стратегия максимально аккуратная:

    1. Пытаемся использовать сведения Core Audio:
       сначала ищем default‑устройство среди всех девайсов по их флагам,
       которые даёт Pycaw.
    2. Если это не сработало, пробуем сопоставить текущее default‑устройство
       по «подписи» громкости+mute: берём текущую громкость default‑девайса
       и ищем среди устройств вывода то, у которого такие же значения.
    3. В качестве фолбэка — аккуратно пробуем вытащить имя через
       AudioUtilities.GetSpeakers() и свойства устройства.
    4. Если и это не удалось, а устройство вывода всего одно — используем его имя.
       В противном случае возвращаем None, чтобы не врать пользователю.
    """

    def _extract_name(dev):
        """Аккуратно достаём человекочитаемое имя устройства."""
        if dev is None:
            return None

        # Прямые атрибуты Pycaw
        for attr in ("FriendlyName", "friendly_name", "DeviceFriendlyName", "name"):
            try:
                val = getattr(dev, attr, None)
            except Exception:
                continue
            if isinstance(val, str):
                val = val.strip()
            if val:
                return val

        # Иногда имя лежит в словаре properties
        try:
            props = getattr(dev, "properties", None)
            if isinstance(props, dict):
                for k, v in props.items():
                    if not isinstance(v, str):
                        continue
                    vs = v.strip()
                    if vs:
                        return vs
        except Exception:
            pass

        return None

    # Сначала попробуем получить список устройств вывода,
    # чтобы потом можно было по нему сопоставлять ID.
    try:
        playback_devices = _list_playback_devices()
    except Exception as e:
        logger.debug(
            "_get_default_playback_device_name(): ошибка _list_playback_devices() при подготовке: %s",
            e,
        )
        playback_devices = []

    # Попробуем найти default‑устройство среди всех девайсов по их флагам
    try:
        devices = AudioUtilities.GetAllDevices()
    except Exception as e:
        logger.debug("_get_default_playback_device_name(): ошибка GetAllDevices(): %s", e)
        devices = []

    try:
        from pycaw.constants import DEVICE_STATE, EDataFlow  # type: ignore
    except Exception:
        DEVICE_STATE = None  # type: ignore
        EDataFlow = None     # type: ignore

    def _is_render(df_value):
        """Является ли устройство устройством ВЫВОДА (Render)."""
        try:
            if EDataFlow is not None and isinstance(df_value, EDataFlow):
                return df_value == EDataFlow.eRender
        except Exception:
            pass
        try:
            iv = int(df_value)
            # В Pycaw / Core Audio eRender обычно == 0
            return iv == 0
        except Exception:
            pass
        return False

    def _is_active(st_value):
        """Активно ли устройство."""
        try:
            if DEVICE_STATE is not None:
                try:
                    iv = int(st_value)
                    return bool(iv & int(DEVICE_STATE.ACTIVE))
                except Exception:
                    pass
        except Exception:
            pass
        # Не смогли надёжно определить — считаем неактивным
        return False

    # --- Шаг 1: пробуем найти default‑девайс по флагам, которые даёт Pycaw ---
    for dev in devices:
        try:
            df = getattr(dev, "data_flow", getattr(dev, "DataFlow", None))
            st = getattr(dev, "state", getattr(dev, "State", None))
            if not _is_render(df) or not _is_active(st):
                continue

            is_default = False
            # Pycaw иногда даёт флаги default в атрибутах
            for attr in dir(dev):
                if "default" not in attr.lower():
                    continue
                try:
                    val = getattr(dev, attr)
                except Exception:
                    continue
                try:
                    if bool(val):
                        is_default = True
                        break
                except Exception:
                    continue

            if not is_default:
                continue

            name = _extract_name(dev)
            if name:
                logger.debug(
                    "_get_default_playback_device_name(): имя через флаги default: %r",
                    name,
                )
                return name
        except Exception as e:
            logger.debug(
                "_get_default_playback_device_name(): ошибка обработки устройства при поиске default‑флагов: %s",
                e,
            )

    # --- Шаг 2: сопоставление по «подписи» громкости + mute ---
    current_scalar = None
    current_mute = None
    try:
        vol_iface, dev_obj = _get_volume_interface()
        current_scalar = float(vol_iface.GetMasterVolumeLevelScalar())
        current_mute = bool(vol_iface.GetMute())
        logger.debug(
            "_get_default_playback_device_name(): подпись громкости default‑устройства: scalar=%f, mute=%s",
            current_scalar,
            current_mute,
        )
    except Exception as e:
        logger.debug(
            "_get_default_playback_device_name(): не удалось получить подпись громкости default‑устройства: %s",
            e,
        )

    if playback_devices and current_scalar is not None:
        ids_of_interest = {d.get("id") for d in playback_devices if d.get("id")}
        TOL = 0.005

        for dev in devices:
            try:
                dev_id = getattr(dev, "id", None)
                if not dev_id or dev_id not in ids_of_interest:
                    continue

                # Пытаемся получить интерфейс громкости для конкретного устройства.
                # На новых версиях Pycaw используем dev.EndpointVolume, на старых — dev.Activate().
                v = None
                try:
                    endpoint = getattr(dev, "EndpointVolume", None)
                    if endpoint is not None:
                        try:
                            v = endpoint.QueryInterface(IAudioEndpointVolume)
                        except Exception:
                            v = endpoint
                    else:
                        raise AttributeError("EndpointVolume is None")
                except AttributeError:
                    interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    v = cast(interface, POINTER(IAudioEndpointVolume))
                scalar = float(v.GetMasterVolumeLevelScalar())
                mute = bool(v.GetMute())

                if abs(scalar - current_scalar) <= TOL and mute == current_mute:
                    # Нашли устройство с той же громкостью и mute
                    for info in playback_devices:
                        if info.get("id") == dev_id:
                            name = info.get("name") or info.get("label")
                            if name:
                                logger.debug(
                                    "_get_default_playback_device_name(): имя через сопоставление громкости: %r (id=%r)",
                                    name,
                                    dev_id,
                                )
                                return name
            except Exception as e:
                logger.debug(
                    "_get_default_playback_device_name(): ошибка при сопоставлении устройства id=%r: %s",
                    getattr(dev, "id", None),
                    e,
                )

    # --- Шаг 3: аккуратный фолбэк на GetSpeakers() ---
    logger.debug("_get_default_playback_device_name(): попытка через GetSpeakers() (фолбэк)")
    device = None
    try:
        device = AudioUtilities.GetSpeakers()
    except Exception as e:
        logger.debug("_get_default_playback_device_name(): ошибка GetSpeakers(): %s", e)
        device = None

    name = _extract_name(device)
    if name:
        logger.debug("_get_default_playback_device_name(): имя через GetSpeakers(): %r", name)
        return name

    # --- Шаг 4: если в системе всего одно устройство вывода, берём его имя ---
    if playback_devices and len(playback_devices) == 1:
        name = playback_devices[0].get("name") or playback_devices[0].get("label")
        if name:
            logger.debug(
                "_get_default_playback_device_name(): единственное устройство вывода: %r",
                name,
            )
            return name

    logger.warning(
        "_get_default_playback_device_name(): не удалось надёжно определить устройство воспроизведения по умолчанию",
    )
    return None


def _list_playback_devices():
    """Вернуть список устройств ВЫВОДА: [{"name": str, "id": str, "label": str}, ...].

    В список попадают только:
    - устройства с направлением Render (вывод);
    - устройства в состоянии ACTIVE.
    Отключённые, «not present», «stereo mix», микрофоны и прочее стараемся не тянуть.
    """
    logger.debug("_list_playback_devices(): начинаю перечисление устройств вывода")
    devices_out = []
    seen = set()

    try:
        devices = AudioUtilities.GetAllDevices()
    except Exception as e:
        logger.exception("_list_playback_devices(): ошибка GetAllDevices(): %s", e)
        devices = []

    # Пытаемся подтянуть константы из Pycaw, если они доступны
    try:
        from pycaw.constants import DEVICE_STATE, EDataFlow  # type: ignore
    except Exception:
        DEVICE_STATE = None  # type: ignore
        EDataFlow = None     # type: ignore

    def _is_render(dev, df_value):
        """Понимаем, является ли устройство устройством ВЫВОДА (Render)."""
        try:
            if EDataFlow is not None and isinstance(df_value, EDataFlow):
                return df_value == EDataFlow.eRender
        except Exception:
            pass

        # В Pycaw/WinAPI eRender обычно == 0
        try:
            iv = int(df_value)
            if iv == 0:
                return True
        except Exception:
            pass

        # Как запасной вариант — по имени устройства
        try:
            name = getattr(dev, "FriendlyName", None) or getattr(dev, "DeviceFriendlyName", None) or ""
            s = str(name).lower()
            # типичные слова для выходных устройств
            if any(word in s for word in ("speaker", "динамик", "наушник", "headphone", "hdmi", "display audio", "аудио")):
                return True
        except Exception:
            pass

        # Если ничего не поняли — лучше НЕ включать в список, чем добавить микрофон
        return False

    def _is_active(dev, st_value):
        """Понимаем, активно ли устройство (по возможности)."""
        try:
            if DEVICE_STATE is not None:
                try:
                    iv = int(st_value)
                    return bool(iv & int(DEVICE_STATE.ACTIVE))
                except Exception:
                    pass
        except Exception:
            pass

        # Попробуем разобрать строковое представление
        try:
            s = str(st_value).lower()
            if "active" in s:
                return True
            if any(word in s for word in ("disabled", "not present", "unplugged")):
                return False
        except Exception:
            pass

        # По умолчанию считаем неактивным, чтобы не тянуть мусор
        return False

    for dev in devices:
        try:
            data_flow = getattr(dev, "data_flow", getattr(dev, "DataFlow", None))
            if not _is_render(dev, data_flow):
                continue

            state = getattr(dev, "state", getattr(dev, "State", None))
            if not _is_active(dev, state):
                continue

            # Имя устройства
            name = None
            for attr in ("FriendlyName", "friendly_name", "DeviceFriendlyName", "name"):
                try:
                    val = getattr(dev, attr, None)
                except Exception:
                    continue
                if isinstance(val, str):
                    val = val.strip()
                if val:
                    name = val
                    break

            if not name:
                continue

            dev_id = getattr(dev, "id", None)
            key = dev_id or name
            if key in seen:
                continue
            seen.add(key)

            devices_out.append({"name": name, "id": dev_id, "label": name})
        except Exception as e:
            logger.debug("_list_playback_devices(): ошибка обработки устройства: %s", e)
            continue

    # Фолбэк: если ничего не нашли, хотя бы одно устройство — текущее default
    if not devices_out:
        logger.warning("_list_playback_devices(): список пуст, пробую фолбэк через GetSpeakers()")
        try:
            dev = AudioUtilities.GetSpeakers()
            for attr in ("FriendlyName", "friendly_name", "DeviceFriendlyName", "name"):
                val = getattr(dev, attr, None)
                if isinstance(val, str):
                    val = val.strip()
                if val:
                    dev_id = getattr(dev, "id", None)
                    devices_out.append({"name": val, "id": dev_id, "label": val})
                    break
        except Exception as e:
            logger.exception("_list_playback_devices(): ошибка фолбэка через GetSpeakers(): %s", e)

    logger.debug("_list_playback_devices(): найдено %d устройств вывода", len(devices_out))
    return devices_out


# Кэш интерфейса громкости, чтобы не плодить COM‑объекты.
# Сбрасывается при смене устройства по умолчанию и при ошибках в _get_volume_interface().
_VOLUME_IFACE = None
_VOLUME_DEVICE = None


def _get_volume_interface():
    """
    Получить интерфейс управления громкостью для текущего
    системного устройства воспроизведения по умолчанию.

    ВАЖНО:
    - Мы кэшируем IAudioEndpointVolume, чтобы не плодить COM‑объекты.
      У pycaw/comtypes есть баги при частом создании/освобождении, это
      может приводить к случайным падениям процесса.
    - При смене устройства по умолчанию кэш сбрасывается в
      _set_default_playback_device().
    - Если при работе с кэшированным интерфейсом прилетает ошибка,
      мы пробуем пересоздать его максимум один раз.
    """
    from comtypes import COMError  # локальный импорт

    global _VOLUME_IFACE, _VOLUME_DEVICE

    # Если уже есть рабочий интерфейс — пробуем использовать его
    if _VOLUME_IFACE is not None:
        try:
            # Лёгкая проверка, что объект ещё живой
            _ = float(_VOLUME_IFACE.GetMasterVolumeLevelScalar())
            logger.debug(
                "_get_volume_interface(): используем кэшированный интерфейс для устройства %r (id=%r)",
                getattr(_VOLUME_DEVICE, "FriendlyName", None),
                getattr(_VOLUME_DEVICE, "id", None),
            )
            return _VOLUME_IFACE, _VOLUME_DEVICE
        except Exception as e:
            logger.warning(
                "_get_volume_interface(): кэшированный интерфейс невалиден, пересоздаю: %s",
                e,
            )
            _VOLUME_IFACE = None
            _VOLUME_DEVICE = None

    last_exc = None
    for attempt in range(2):
        try:
            logger.debug("_get_volume_interface(): попытка инициализации #%d", attempt + 1)
            device = AudioUtilities.GetSpeakers()

            volume = None

            # --- Новый API Pycaw: AudioDevice.EndpointVolume ---
            try:
                endpoint = getattr(device, "EndpointVolume", None)
                if endpoint is not None:
                    logger.debug(
                        "_get_volume_interface(): использую device.EndpointVolume (новый API Pycaw)"
                    )
                    try:
                        # На новых версиях EndpointVolume уже даёт нужный COM-интерфейс,
                        # но для надёжности пробуем запросить именно IAudioEndpointVolume
                        volume = endpoint.QueryInterface(IAudioEndpointVolume)
                    except Exception:
                        volume = endpoint
                else:
                    raise AttributeError("EndpointVolume is None")
            except AttributeError:
                # --- Старый API Pycaw: через Activate() ---
                logger.debug(
                    "_get_volume_interface(): EndpointVolume отсутствует, использую device.Activate() (старый API)"
                )
                interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                volume = cast(interface, POINTER(IAudioEndpointVolume))

            _VOLUME_IFACE = volume
            _VOLUME_DEVICE = device
            logger.debug(
                "_get_volume_interface(): интерфейс инициализирован для устройства %r (id=%r)",
                getattr(device, "FriendlyName", None),
                getattr(device, "id", None),
            )
            return volume, device
        except COMError as e:
            last_exc = e
            logger.exception("_get_volume_interface(): COMError: %s", e)
            _VOLUME_IFACE = None
            _VOLUME_DEVICE = None
        except Exception as e:
            last_exc = e
            logger.exception("_get_volume_interface(): ошибка: %s", e)
            _VOLUME_IFACE = None
            _VOLUME_DEVICE = None

    logger.error("_get_volume_interface(): не удалось инициализировать интерфейс управления громкостью")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Не удалось инициализировать интерфейс управления громкостью")
def _get_current_volume_and_mute():
    """
    Возвращает (уровень громкости в процентах, is_muted: bool, имя устройства).
    Полностью работает через системный интерфейс Windows (Core Audio / IAudioEndpointVolume).
    """
    current_vol = 0
    is_muted = False
    dev_name = None

    try:
        vol_iface, dev_obj = _get_volume_interface()
        current_vol = int(round(vol_iface.GetMasterVolumeLevelScalar() * 100))
        is_muted = bool(vol_iface.GetMute())
        dev_name = getattr(dev_obj, "FriendlyName", None)
    except Exception as e:
        logger.exception("_get_current_volume_and_mute(): ошибка получения громкости: %s", e)

    if not dev_name:
        dev_name = _get_default_playback_device_name()

    if not dev_name:
        dev_name = "Не удалось определить устройство"

    logger.debug(
        "_get_current_volume_and_mute(): volume=%d%%, muted=%s, device=%r",
        current_vol,
        is_muted,
        dev_name,
    )

    return current_vol, is_muted, dev_name


def _nircmd_change_volume(step_percent: int):
    """
    Меняет системную громкость на указанное количество процентов
    через системный интерфейс Windows (Core Audio / IAudioEndpointVolume).

    step_percent может быть положительным или отрицательным.
    Возвращает (успех: bool, подробности: str).
    """
    logger.info("_nircmd_change_volume(): запрос изменения громкости на %+d%%", step_percent)
    try:
        volume, dev_obj = _get_volume_interface()
        current_scalar = float(volume.GetMasterVolumeLevelScalar())
        current_percent = int(round(current_scalar * 100))

        if step_percent == 0:
            msg = f"Изменение громкости на 0%: текущее значение {current_percent}%."
            logger.debug("_nircmd_change_volume(): %s", msg)
            return True, msg

        new_percent = max(0, min(100, current_percent + step_percent))
        new_scalar = new_percent / 100.0
        volume.SetMasterVolumeLevelScalar(new_scalar, None)

        msg = (
            f"Громкость изменена с {current_percent}% до {new_percent}% "
            f"через системный аудио-интерфейс Windows."
        )
        logger.info("_nircmd_change_volume(): %s", msg)
        return True, msg
    except Exception as e:
        msg = f"Ошибка изменения громкости через системный аудио-интерфейс: {e}"
        logger.exception("_nircmd_change_volume(): %s", msg)
        return False, msg


def _nircmd_set_mute(mute: bool):
    """
    Включает или выключает системный звук через системный интерфейс Windows (Core Audio).
    Возвращает (успех: bool, подробности: str).
    """
    logger.info("_nircmd_set_mute(): запрос mute=%s", mute)
    try:
        volume, dev_obj = _get_volume_interface()
        volume.SetMute(bool(mute), None)
        state = "выключен" if mute else "включён"
        msg = f"Системный звук {state} через системный аудио-интерфейс Windows."
        logger.info("_nircmd_set_mute(): %s", msg)
        return True, msg
    except Exception as e:
        msg = f"Ошибка управления mute через системный аудио-интерфейс: {e}"
        logger.exception("_nircmd_set_mute(): %s", msg)
        return False, msg


# ================== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ==================

def register_volume_handlers(dp: Dispatcher, get_sound_keyboard_cb):
    """
    Регистрирует хендлеры меню 'Громкость'.
    get_sound_keyboard_cb: функция без аргументов, возвращает клавиатуру со звуковыми функциями.
    """
    logger.debug("register_volume_handlers(): регистрация хендлеров громкости")

    def sound_kb():
        try:
            return get_sound_keyboard_cb()
        except Exception as e:
            logger.exception("sound_kb(): ошибка получения клавиатуры звуковых функций: %s", e)
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add(KeyboardButton("Вернуться"))
            return kb

    # ✅ Команда из Bot Menu: /volume
    # Открывает то же меню, что и кнопка/текст "Громкость".
    @dp.message_handler(commands=["volume"], state="*")
    async def volume_command_handler(message: types.Message):
        try:
            chat_id = message.chat.id
            logger.debug("volume_command_handler(): chat_id=%s, text=%r", chat_id, message.text)
        except Exception:
            pass

        # Сбрасывать состояния не будем: если пользователь был в выборе устройства,
        # пусть продолжит по кнопкам как и раньше.
        try:
            current_vol, is_muted, dev_name = _get_current_volume_and_mute()
            await message.answer(
                f"Текущая громкость: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}\n"
                f"Устройство по умолчанию: {dev_name}",
                reply_markup=_get_volume_control_keyboard(is_muted)
            )
        except Exception as e:
            try:
                logger.exception("volume_command_handler(): ошибка открытия меню громкости: %s", e)
            except Exception:
                pass
            await message.answer(
                "Не удалось открыть меню «Громкость». Попробуй нажать кнопку «Громкость» ещё раз.",
                reply_markup=sound_kb()
            )


    @dp.message_handler(
        lambda m:
            m.text in [
                "Громкость", "Увеличить громкость", "Уменьшить громкость",
                "Включить звук", "Выключить звук",
                "Сменить устройство воспроизведения",
                "Вернуться в доп.меню", "На главную"
            ] or m.chat.id in AUDIO_OUTPUT_STATE,
        content_types=["text"]
    )
    async def volume_button_handler(message: types.Message):
        try:
            text = message.text
            chat_id = message.chat.id
    
            logger.debug(
                "volume_button_handler(): chat_id=%s, text=%r, state=%r",
                chat_id,
                text,
                AUDIO_OUTPUT_STATE.get(chat_id),
            )
    
            # --- Режим выбора устройства вывода ---
            if chat_id in AUDIO_OUTPUT_STATE:
                st = AUDIO_OUTPUT_STATE.get(chat_id, {})
                if st.get("state") == "select_output":
                    logger.debug("volume_button_handler(): режим выбора устройства вывода для chat_id=%s", chat_id)

                    # Если пользователь прислал команду (/...), значит он, вероятно, уже ушёл в другой модуль/команду.
                    # Сбрасываем наш режим выбора устройства и пропускаем сообщение дальше, чтобы его обработал нужный хендлер.
                    if isinstance(text, str) and text.startswith("/") and SkipHandler is not None:
                        AUDIO_OUTPUT_STATE.pop(chat_id, None)
                        raise SkipHandler()

                    # Глобальная навигация даже в режиме выбора устройства (на случай, если кнопки пришли из другого меню)
                    if text == "На главную":
                        AUDIO_OUTPUT_STATE.pop(chat_id, None)
                        await message.answer(
                            "Возвращаюсь на главную.",
                            reply_markup=get_main_keyboard()
                        )
                        return

                    if text == "Вернуться в доп.меню":
                        AUDIO_OUTPUT_STATE.pop(chat_id, None)
                        await message.answer(
                            "Возвращаюсь в дополнительное меню.",
                            reply_markup=get_additional_keyboard()
                        )
                        return

                    if text == "Громкость":
                        AUDIO_OUTPUT_STATE.pop(chat_id, None)
                        current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                        await message.answer(
                            f"Текущая громкость: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}\n"
                            f"Устройство по умолчанию: {dev_name}",
                            reply_markup=_get_volume_control_keyboard(is_muted)
                        )
                        return

                    if text == "Отмена":
                        AUDIO_OUTPUT_STATE.pop(chat_id, None)
                        try:
                            vol_iface, dev = _get_volume_interface()
                            is_muted = bool(vol_iface.GetMute())
                        except Exception as e:
                            logger.exception(
                                "volume_button_handler(): ошибка получения mute после отмены выбора устройства: %s",
                                e,
                            )
                            is_muted = False
                        await message.answer(
                            "Отмена выбора устройства.",
                            reply_markup=_get_volume_control_keyboard(is_muted)
                        )
                        return
    
                    # Пытаемся распарсить "N. Название"
                    try:
                        if "." in text:
                            num_str = text.split(".", 1)[0].strip()
                            idx = int(num_str) - 1
                            devices = st.get("devices", [])
                            if idx < 0 or idx >= len(devices):
                                raise ValueError
    
                            chosen = devices[idx]
                            device_name = chosen.get("name", "Аудио устройство")
                            device_id = chosen.get("id")
    
                            logger.info(
                                "volume_button_handler(): chat_id=%s выбрал устройство #%d: %r (id=%r)",
                                chat_id,
                                idx + 1,
                                device_name,
                                device_id,
                            )
    
                            # Переключаем системное устройство по умолчанию через PolicyConfig
                            success, details = _set_default_playback_device(device_id)
    
                            # Обновим состояние громкости после переключения (или попытки)
                            try:
                                vol_iface, dev = _get_volume_interface()
                                is_muted = bool(vol_iface.GetMute())
                                dev_name = _get_default_playback_device_name() or getattr(dev, "FriendlyName", None) or device_name
                            except Exception as e:
                                logger.exception(
                                    "volume_button_handler(): ошибка получения статуса после смены устройства: %s",
                                    e,
                                )
                                is_muted = False
                                dev_name = _get_default_playback_device_name() or device_name
    
                            AUDIO_OUTPUT_STATE.pop(chat_id, None)
    
                            if success:
                                await message.answer(
                                    f"Устройство по умолчанию переключено: {dev_name}\n\n"
                                    f"Подробности (PolicyConfig COM):\n{details}",
                                    reply_markup=_get_volume_control_keyboard(is_muted)
                                )
                            else:
                                await message.answer(
                                    "Не удалось переключить устройство через системный аудио-интерфейс Windows.\n"
                                    f"{details}",
                                    reply_markup=_get_volume_control_keyboard(is_muted)
                                )
    
                            return
                    except Exception as e:
                        logger.exception(
                            "volume_button_handler(): ошибка обработки выбора устройства (chat_id=%s, text=%r): %s",
                            chat_id,
                            text,
                            e,
                        )
    
                    # Если не распарсили — повторим клавиатуру
                    devices = st.get("devices", [])
                    default_name = _get_default_playback_device_name()
                    await message.answer(
                        "Пожалуйста, выберите устройство из списка ниже:",
                        reply_markup=_get_output_devices_keyboard(devices, default_name)
                    )
                    return
    
            # --- Основной вход в меню громкости ---
            if text == "Громкость":
                logger.debug("volume_button_handler(): вход в меню 'Громкость' (chat_id=%s)", chat_id)
                current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                await message.answer(
                    f"Текущая громкость: {current_vol}%, Звук {'выключен' if is_muted else 'включён'}\n"
                    f"Устройство по умолчанию: {dev_name}",
                    reply_markup=_get_volume_control_keyboard(is_muted)
                )
                return
    
            # --- Управление уровнем / mute ---
            if text == "Увеличить громкость":
                logger.debug("volume_button_handler(): 'Увеличить громкость' (chat_id=%s)", chat_id)
                ok, details = _nircmd_change_volume(10)
                current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                if not ok:
                    await message.answer(
                        "Не удалось изменить громкость через системный аудио-интерфейс.\n"
                        f"{details}\n\n"
                        f"Текущая громкость: {current_vol}%, "
                        f"Звук {'выключен' if is_muted else 'включён'}\n"
                        f"Устройство по умолчанию: {dev_name}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                else:
                    await message.answer(
                        f"Громкость изменена: {current_vol}%, "
                        f"Звук {'выключен' if is_muted else 'включён'}\n"
                        f"Устройство по умолчанию: {dev_name}\n\n"
                        f"Технические детали:\n{details}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                return
    
            if text == "Уменьшить громкость":
                logger.debug("volume_button_handler(): 'Уменьшить громкость' (chat_id=%s)", chat_id)
                ok, details = _nircmd_change_volume(-10)
                current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                if not ok:
                    await message.answer(
                        "Не удалось изменить громкость через системный аудио-интерфейс.\n"
                        f"{details}\n\n"
                        f"Текущая громкость: {current_vol}%, "
                        f"Звук {'выключен' if is_muted else 'включён'}\n"
                        f"Устройство по умолчанию: {dev_name}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                else:
                    await message.answer(
                        f"Громкость изменена: {current_vol}%, "
                        f"Звук {'выключен' if is_muted else 'включён'}\n"
                        f"Устройство по умолчанию: {dev_name}\n\n"
                        f"Технические детали:\n{details}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                return
    
            if text == "Включить звук":
                logger.debug("volume_button_handler(): 'Включить звук' (chat_id=%s)", chat_id)
                try:
                    ok, details = _nircmd_set_mute(False)
                    current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                except Exception as e:
                    logger.exception("volume_button_handler(): ошибка при включении звука: %s", e)
                    await message.answer(
                        f"Ошибка при включении звука: {e}",
                        reply_markup=_get_volume_control_keyboard(False)
                    )
                    return
    
                if not ok:
                    await message.answer(
                        "Не удалось включить звук через системный аудио-интерфейс.\n"
                        f"{details}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                else:
                    await message.answer(
                        f"Звук включён. Громкость: {current_vol}%\n"
                        f"Устройство по умолчанию: {dev_name}\n\n"
                        f"Технические детали:\n{details}",
                        reply_markup=_get_volume_control_keyboard(False)
                    )
                return
    
            if text == "Выключить звук":
                logger.debug("volume_button_handler(): 'Выключить звук' (chat_id=%s)", chat_id)
                try:
                    ok, details = _nircmd_set_mute(True)
                    current_vol, is_muted, dev_name = _get_current_volume_and_mute()
                except Exception as e:
                    logger.exception("volume_button_handler(): ошибка при выключении звука: %s", e)
                    await message.answer(
                        f"Ошибка при выключении звука: {e}",
                        reply_markup=_get_volume_control_keyboard(True)
                    )
                    return
    
                if not ok:
                    await message.answer(
                        "Не удалось выключить звук через системный аудио-интерфейс.\n"
                        f"{details}",
                        reply_markup=_get_volume_control_keyboard(is_muted)
                    )
                else:
                    await message.answer(
                        f"Звук выключен. Громкость: {current_vol}%\n"
                        f"Устройство по умолчанию: {dev_name}\n\n"
                        f"Технические детали:\n{details}",
                        reply_markup=_get_volume_control_keyboard(True)
                    )
                return
    
            if text == "Сменить устройство воспроизведения":
                logger.debug("volume_button_handler(): 'Сменить устройство воспроизведения' (chat_id=%s)", chat_id)
                devices = _list_playback_devices()
                if not devices:
                    default_name = _get_default_playback_device_name()
                    extra = f"\nТекущее устройство по умолчанию: {default_name}" if default_name else ""
                    await message.answer(
                        "Не удалось получить список устройств воспроизведения." + extra,
                        reply_markup=_get_volume_control_keyboard(False)
                    )
                    return
    
                AUDIO_OUTPUT_STATE[chat_id] = {"state": "select_output", "devices": devices}
                default_name = _get_default_playback_device_name()
                if default_name:
                    text_header = (
                        f"Текущее устройство по умолчанию: {default_name}\n\n"
                        "Выберите устройство воспроизведения.\n"
                        "Переключение выполняется через системный аудио-интерфейс Windows."
                    )
                else:
                    text_header = (
                        "Выберите устройство воспроизведения.\n"
                        "Переключение выполняется через системный аудио-интерфейс Windows."
                    )
    
                await message.answer(
                    text_header,
                    reply_markup=_get_output_devices_keyboard(devices, default_name)
                )
                return
    
            # --- Навигация ---
            if text == "Вернуться в доп.меню":
                logger.debug("volume_button_handler(): 'Вернуться в доп.меню' (chat_id=%s)", chat_id)
                await message.answer(
                    "Возвращаюсь в дополнительное меню.",
                    reply_markup=get_additional_keyboard()
                )
                return
    
            if text == "На главную":
                logger.debug("volume_button_handler(): 'На главную' (chat_id=%s)", chat_id)
                await message.answer(
                    "Возвращаюсь на главную.",
                    reply_markup=get_main_keyboard()
                )
                return
    
        except Exception as e:
            logger.exception("volume_button_handler(): необработанная ошибка: %s", e)
            try:
                await message.answer(
                    "Произошла непредвиденная ошибка модуля громкости.\n"
                    "Попробуй ещё раз открыть меню «Громкость».",
                    reply_markup=sound_kb()
                )
            except Exception:
                pass
# ================== РЕГИСТРАЦИЯ ДЛЯ MODULS_MANAGER_EXT ==================

_HANDLERS_REGISTERED = False

def register_handlers(dp: Dispatcher):
    """Обёртка для Moduls_manager_ext.

    Moduls_manager_ext ожидает, что модуль будет иметь функцию
    register_handlers(dp). Здесь мы один раз вызываем register_volume_handlers,
    передавая коллбек клавиатуры.
    """
    global _HANDLERS_REGISTERED
    if _HANDLERS_REGISTERED:
        try:
            logger.debug("register_handlers(): хендлеры уже зарегистрированы, повторный вызов пропущен")
        except Exception:
            pass
        return

    def get_sound_keyboard_cb():
        """Коллбек-клавиатура по умолчанию.

        Если специализированное меню звука недоступно,
        возвращаем главное меню, чтобы пользователь не застрял.
        """
        try:
            return get_main_keyboard()
        except Exception as e:
            try:
                logger.exception("register_handlers(): ошибка получения главной клавиатуры: %s", e)
            except Exception:
                pass
            kb = ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add(KeyboardButton("Вернуться"))
            return kb

    try:
        logger.debug("register_handlers(): первичная регистрация хендлеров громкости")
    except Exception:
        pass

    register_volume_handlers(dp, get_sound_keyboard_cb)
    _HANDLERS_REGISTERED = True