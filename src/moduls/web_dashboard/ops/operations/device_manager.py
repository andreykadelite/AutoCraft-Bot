from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any, Dict, List, Tuple

_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL_SECONDS = 12.0
_CACHE_LOCK = threading.Lock()
_PS_TIMEOUT_SECONDS = 35


def _invalidate_cache() -> None:
    _CACHE["ts"] = 0.0
    _CACHE["data"] = None


def _run_powershell_json(script: str) -> Tuple[Any | None, str | None]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"PowerShell превысил {_PS_TIMEOUT_SECONDS} сек."
    except Exception as exc:
        return None, str(exc)

    stdout = (result.stdout or "").strip().lstrip("\ufeff")
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return None, stderr or stdout or f"PowerShell вернул код {result.returncode}"
    if not stdout:
        return None, stderr or "Нет данных PowerShell"
    try:
        return json.loads(stdout), None
    except Exception as exc:
        return None, f"Не удалось разобрать JSON PowerShell: {exc}"


def _run_powershell(script: str) -> Dict[str, object]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"PowerShell превысил {_PS_TIMEOUT_SECONDS} сек."}
    except Exception as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return {"ok": False, "stdout": stdout, "stderr": stderr or stdout or f"Код {result.returncode}"}
    return {"ok": True, "stdout": stdout, "stderr": stderr}


def _ensure_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    text = str(value)
    return [text] if text.strip() else []


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_device(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "instance_id": _coerce_text(raw.get("InstanceId") or raw.get("instance_id")),
        "name": _coerce_text(raw.get("Name") or raw.get("name")),
        "class_name": _coerce_text(raw.get("Class") or raw.get("class_name") or raw.get("ClassGuid")),
        "status": _coerce_text(raw.get("Status") or raw.get("status")),
        "problem_code": raw.get("Problem") if raw.get("Problem") is not None else raw.get("problem_code"),
        "manufacturer": _coerce_text(raw.get("Manufacturer") or raw.get("manufacturer")),
        "present": raw.get("Present") if raw.get("Present") is not None else raw.get("present"),
        "config_error": raw.get("ConfigError") if raw.get("ConfigError") is not None else raw.get("config_error"),
        "hardware_id": _ensure_list(raw.get("HardwareId") or raw.get("hardware_id")),
        "location": _coerce_text(raw.get("Location") or raw.get("location")),
        "driver_version": _coerce_text(raw.get("DriverVersion") or raw.get("driver_version")),
        "driver_date": _coerce_text(raw.get("DriverDate") or raw.get("driver_date")),
        "driver_provider": _coerce_text(raw.get("DriverProvider") or raw.get("driver_provider")),
        "driver_inf": _coerce_text(raw.get("DriverInf") or raw.get("driver_inf")),
        "service": _coerce_text(raw.get("Service") or raw.get("service")),
    }


def list_devices(force: bool = False) -> Dict[str, object]:
    now = time.monotonic()
    cached = _CACHE.get("data")
    if not force and cached and (now - _CACHE.get("ts", 0.0) < _CACHE_TTL_SECONDS):
        return {"ok": True, "data": cached, "stdout": "", "stderr": ""}

    if not _CACHE_LOCK.acquire(blocking=False):
        return {"ok": True, "data": cached or [], "stdout": "", "stderr": ""}

    try:
        script = r"""
$ErrorActionPreference = "Stop";
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;

$devices = @();
$usePnp = Get-Command Get-PnpDevice -ErrorAction SilentlyContinue;
if ($usePnp) {
  $devices = Get-PnpDevice | Select-Object InstanceId,FriendlyName,Name,Class,Status,Problem,Manufacturer,Present;
  $entityMap = @{};
  try {
    $entities = Get-CimInstance Win32_PnPEntity | Select-Object PNPDeviceID,HardwareID,LocationInformation,ConfigManagerErrorCode,Status,Service;
    foreach ($e in $entities) { $entityMap[$e.PNPDeviceID] = $e }
  } catch {}

  $driverMap = @{};
  try {
    $drivers = Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceID,DriverVersion,DriverDate,DriverProviderName,InfName;
    foreach ($d in $drivers) { $driverMap[$d.DeviceID] = $d }
  } catch {}

  $devices = $devices | ForEach-Object {
    $id = $_.InstanceId;
    $name = if ($_.FriendlyName) { $_.FriendlyName } else { $_.Name };
    $ent = $entityMap[$id];
    $drv = $driverMap[$id];
    $drvDate = $null;
    if ($drv -and $drv.DriverDate) {
      try { $drvDate = ([Management.ManagementDateTimeConverter]::ToDateTime($drv.DriverDate)).ToString("o") } catch { $drvDate = $drv.DriverDate }
    }
    [pscustomobject]@{
      InstanceId=$id;
      Name=$name;
      Class=$_.Class;
      Status=$_.Status;
      Problem=$_.Problem;
      Manufacturer=$_.Manufacturer;
      Present=$_.Present;
      ConfigError=if ($ent) { $ent.ConfigManagerErrorCode } else { $null };
      HardwareId=if ($ent) { $ent.HardwareID } else { $null };
      Location=if ($ent) { $ent.LocationInformation } else { $null };
      Service=if ($ent) { $ent.Service } else { $null };
      DriverVersion=if ($drv) { $drv.DriverVersion } else { $null };
      DriverDate=$drvDate;
      DriverProvider=if ($drv) { $drv.DriverProviderName } else { $null };
      DriverInf=if ($drv) { $drv.InfName } else { $null };
    }
  }
} else {
  $devices = Get-CimInstance Win32_PnPEntity | Select-Object PNPDeviceID,Name,Status,ConfigManagerErrorCode,Manufacturer,ClassGuid,Service,HardwareID,LocationInformation;
  $devices = $devices | ForEach-Object {
    [pscustomobject]@{
      InstanceId=$_.PNPDeviceID;
      Name=$_.Name;
      Class=$_.ClassGuid;
      Status=$_.Status;
      Problem=$_.ConfigManagerErrorCode;
      Manufacturer=$_.Manufacturer;
      Present=$true;
      ConfigError=$_.ConfigManagerErrorCode;
      HardwareId=$_.HardwareID;
      Location=$_.LocationInformation;
      Service=$_.Service;
      DriverVersion=$null;
      DriverDate=$null;
      DriverProvider=$null;
      DriverInf=$null;
    }
  }
}

$devices | ConvertTo-Json -Depth 6 -Compress
"""
        data, err = _run_powershell_json(script)
        if err:
            return {"ok": False, "data": [], "stdout": "", "stderr": err}
        devices_raw = data if isinstance(data, list) else []
        normalized = [_normalize_device(item) for item in devices_raw if isinstance(item, dict)]
        normalized.sort(key=lambda x: (x.get("name") or x.get("instance_id") or "").lower())
        _CACHE["ts"] = now
        _CACHE["data"] = normalized
        return {"ok": True, "data": normalized, "stdout": "", "stderr": ""}
    finally:
        _CACHE_LOCK.release()


def enable_device(instance_id: str) -> Dict[str, object]:
    if not instance_id:
        return {"ok": False, "stdout": "", "stderr": "Не указан InstanceId устройства."}
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$id = "
        + json.dumps(instance_id)
        + ";"
        "if (Get-Command Enable-PnpDevice -ErrorAction SilentlyContinue) {"
        "Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop | Out-Null;"
        "\"Устройство включено.\""
        "} else {"
        "throw 'Команда Enable-PnpDevice недоступна.'"
        "}"
    )
    result = _run_powershell(script)
    if result.get("ok"):
        _invalidate_cache()
    return result


def disable_device(instance_id: str) -> Dict[str, object]:
    if not instance_id:
        return {"ok": False, "stdout": "", "stderr": "Не указан InstanceId устройства."}
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$id = "
        + json.dumps(instance_id)
        + ";"
        "if (Get-Command Disable-PnpDevice -ErrorAction SilentlyContinue) {"
        "Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop | Out-Null;"
        "\"Устройство отключено.\""
        "} else {"
        "throw 'Команда Disable-PnpDevice недоступна.'"
        "}"
    )
    result = _run_powershell(script)
    if result.get("ok"):
        _invalidate_cache()
    return result


def restart_device(instance_id: str) -> Dict[str, object]:
    if not instance_id:
        return {"ok": False, "stdout": "", "stderr": "Не указан InstanceId устройства."}
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$id = "
        + json.dumps(instance_id)
        + ";"
        "if (Get-Command Restart-PnpDevice -ErrorAction SilentlyContinue) {"
        "Restart-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop | Out-Null;"
        "\"Устройство перезапущено.\""
        "} else {"
        "throw 'Команда Restart-PnpDevice недоступна.'"
        "}"
    )
    result = _run_powershell(script)
    if result.get("ok"):
        _invalidate_cache()
    return result


def rescan_devices() -> Dict[str, object]:
    script = (
        "$ErrorActionPreference = 'Stop';"
        "if (Get-Command pnputil -ErrorAction SilentlyContinue) {"
        "pnputil /scan-devices | Out-String;"
        "} elseif (Get-Command devcon -ErrorAction SilentlyContinue) {"
        "devcon rescan | Out-String;"
        "} else {"
        "throw 'Не найден pnputil или devcon.'"
        "}"
    )
    result = _run_powershell(script)
    if result.get("ok"):
        _invalidate_cache()
    return result
