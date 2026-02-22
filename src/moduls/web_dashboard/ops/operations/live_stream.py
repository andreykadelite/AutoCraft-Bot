from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore

from ...utils import ensure_dir

try:
    from moduls.stream_control_window import (
        close_stream_control_window,
        show_stream_control_window,
    )
except Exception:
    def show_stream_control_window(owner_id: str, title: str, details: str, stop_callback):  # type: ignore
        return False

    def close_stream_control_window(owner_id: str = "") -> None:  # type: ignore
        return None


@dataclass
class DeviceInfo:
    name: str
    label: str
    source: str = "dshow"
    alt_name: Optional[str] = None


@dataclass
class StreamState:
    running: bool = False
    started_at: float = 0.0
    hls_dir: Optional[Path] = None
    ffmpeg: Optional[subprocess.Popen] = None
    ffmpeg_cmd: Optional[List[str]] = None
    last_error: str = ""
    camera: Optional[DeviceInfo] = None
    audio: Optional[DeviceInfo] = None
    ffmpeg_stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=200))
    ffmpeg_stderr_stop: Optional[threading.Event] = None
    ffmpeg_stderr_thread: Optional[threading.Thread] = None
    opencv_capture: Optional["cv2.VideoCapture"] = None
    opencv_stop: Optional[threading.Event] = None
    opencv_thread: Optional[threading.Thread] = None


_STATE = StreamState()
_STATE_LOCK = threading.Lock()
_WEB_STREAM_WINDOW_OWNER = "web-live-stream"

_STREAM_FPS = 25
_STREAM_GOP = _STREAM_FPS * 2
_STREAM_VIDEO_BITRATE = "2500k"
_STREAM_VIDEO_MAXRATE = "3000k"
_STREAM_VIDEO_BUFSIZE = "5000k"
_STREAM_AUDIO_BITRATE = "128k"
_STREAM_THREAD_QUEUE_SIZE = "1024"
_STREAM_DSHOW_RTBUF = "128M"
_STREAM_AUDIO_RTBUF = "128M"
_STREAM_HLS_TIME = "1"
_STREAM_HLS_LIST_SIZE = "8"


def _decode_best_effort(data: bytes) -> str:
    if not data:
        return ""
    encodings = [
        "utf-8-sig",
        "utf-8",
        locale.getpreferredencoding(False),
        "cp1251",
        "cp866",
        "latin-1",
    ]
    for enc in encodings:
        if not enc:
            continue
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _ffmpeg_candidates(base_dir: str) -> list[Path]:
    base = Path(base_dir) if base_dir else Path.cwd()
    candidates = []
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    for root in (base, base / "moduls", Path.cwd()):
        candidates.append(root / "ffmpeg.exe")
        candidates.append(root / "ffmpeg-7.1" / "bin" / "ffmpeg.exe")
    with suppress(Exception):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "ffmpeg.exe")
        candidates.append(exe_dir / "ffmpeg-7.1" / "bin" / "ffmpeg.exe")
    which = shutil.which("ffmpeg")
    if which:
        candidates.append(Path(which))
    return candidates


def resolve_ffmpeg_path(base_dir: str) -> Optional[str]:
    for candidate in _ffmpeg_candidates(base_dir):
        try:
            if candidate and candidate.is_file():
                return str(candidate)
        except Exception:
            continue
    return None


def _ffmpeg_has_dshow(ffmpeg_path: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-formats"],
            capture_output=True,
            timeout=10,
        )
        out = _decode_best_effort((proc.stdout or b"") + (proc.stderr or b""))
        return " dshow" in out or "\ndshow" in out
    except Exception:
        return False


def _probe_dshow_devices(ffmpeg_path: str) -> Tuple[List[DeviceInfo], List[DeviceInfo]]:
    if not ffmpeg_path:
        return [], []
    try:
        proc = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-list_devices",
                "true",
                "-f",
                "dshow",
                "-i",
                "dummy",
            ],
            capture_output=True,
            timeout=15,
        )
        output = _decode_best_effort((proc.stdout or b"") + (proc.stderr or b""))
    except Exception:
        return [], []

    videos: List[DeviceInfo] = []
    audios: List[DeviceInfo] = []
    current: Optional[str] = None
    last_video_index: Optional[int] = None
    last_audio_index: Optional[int] = None
    last_kind: Optional[str] = None

    for line in output.splitlines():
        if "DirectShow video devices" in line:
            current = "video"
            continue
        if "DirectShow audio devices" in line:
            current = "audio"
            continue

        m_dev = re.search(r'"([^"]+)"\s*\((video|audio)\)', line)
        if m_dev:
            name = (m_dev.group(1) or "").strip()
            kind = (m_dev.group(2) or "").strip().lower()
            if not name:
                continue
            if kind == "video":
                videos.append(DeviceInfo(name=name, label=name, source="dshow"))
                last_video_index = len(videos) - 1
                last_kind = "video"
            else:
                audios.append(DeviceInfo(name=name, label=name, source="dshow"))
                last_audio_index = len(audios) - 1
                last_kind = "audio"
            continue

        if "Alternative name" in line:
            q_alt = re.search(r'"([^"]+)"', line)
            if q_alt:
                alt = (q_alt.group(1) or "").strip()
                if alt:
                    kind = last_kind or current
                    if kind == "video" and last_video_index is not None:
                        videos[last_video_index].alt_name = alt
                    elif kind == "audio" and last_audio_index is not None:
                        audios[last_audio_index].alt_name = alt
            continue

        if current not in ("video", "audio"):
            continue

        q = re.search(r'"([^"]+)"', line)
        if not q:
            continue
        name = (q.group(1) or "").strip()
        if not name:
            continue
        if current == "video":
            videos.append(DeviceInfo(name=name, label=name, source="dshow"))
            last_video_index = len(videos) - 1
            last_kind = "video"
        else:
            audios.append(DeviceInfo(name=name, label=name, source="dshow"))
            last_audio_index = len(audios) - 1
            last_kind = "audio"

    return videos, audios


def _probe_opencv_cameras(max_devices: int = 10) -> List[DeviceInfo]:
    devices: List[DeviceInfo] = []
    if cv2 is None:
        return devices

    backends = []
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_DSHOW", 0))
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_MSMF", 0))
    backends.append(getattr(cv2, "CAP_ANY", 0))

    for idx in range(max_devices):
        opened = False
        cap = None
        w = h = 0
        for be in backends:
            try:
                cap = cv2.VideoCapture(idx, be)
                if cap and cap.isOpened():
                    opened = True
                    w = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3)) or 0)
                    h = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4)) or 0)
                    break
            except Exception:
                cap = None
        if cap is not None:
            with suppress(Exception):
                cap.release()
        if not opened:
            continue
        label = f"Камера #{idx + 1} (opencv, {w}x{h})"
        devices.append(DeviceInfo(name=str(idx), label=label, source="opencv"))
    return devices


def _safe_hls_dir(base_dir: str) -> Path:
    base = Path(base_dir) if base_dir else Path.cwd()
    hls_root = base / "data" / "live_stream"
    ensure_dir(hls_root)
    try:
        return Path(tempfile.mkdtemp(prefix="hls_", dir=hls_root))
    except Exception:
        return Path(tempfile.mkdtemp(prefix="hls_"))


def _hls_output_args(hls_dir: Path) -> Tuple[Path, Path, List[str]]:
    os.makedirs(hls_dir, exist_ok=True)
    m3u8_path = hls_dir / "stream.m3u8"
    segment_path = hls_dir / "segment_%03d.ts"
    out = [
        "-f",
        "hls",
        "-hls_time",
        _STREAM_HLS_TIME,
        "-hls_list_size",
        _STREAM_HLS_LIST_SIZE,
        "-hls_delete_threshold",
        "2",
        "-hls_allow_cache",
        "0",
        "-hls_flags",
        "delete_segments+append_list+independent_segments+omit_endlist+temp_file",
        "-hls_segment_filename",
        str(segment_path),
        str(m3u8_path),
    ]
    return m3u8_path, segment_path, out


def _encoding_output_args(include_audio: bool) -> List[str]:
    out = [
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(_STREAM_FPS),
        "-g",
        str(_STREAM_GOP),
        "-sc_threshold",
        "0",
        "-b:v",
        _STREAM_VIDEO_BITRATE,
        "-maxrate",
        _STREAM_VIDEO_MAXRATE,
        "-bufsize",
        _STREAM_VIDEO_BUFSIZE,
    ]
    if include_audio:
        out.extend(
            [
                "-c:a",
                "aac",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-b:a",
                _STREAM_AUDIO_BITRATE,
            ]
        )
    else:
        out.extend(["-an"])
    return out


def _cleanup_hls(hls_dir: Optional[Path]) -> None:
    if hls_dir and hls_dir.is_dir():
        try:
            shutil.rmtree(hls_dir, ignore_errors=True)
        except Exception:
            pass


def _parse_device_value(value: str) -> Tuple[str, str]:
    raw = (value or "").strip()
    if "::" in raw:
        source, name = raw.split("::", 1)
        source = (source or "dshow").strip().lower()
        name = (name or "").strip()
        if source in ("opencv", "dshow") and name:
            return source, name
    return "dshow", raw


def _start_ffmpeg_dshow(
    ffmpeg_path: str,
    state: StreamState,
    m3u8_path: Path,
    hls_dir: Path,
    video_name: str,
    audio_name: Optional[str],
    extra_input_args: Optional[List[str]] = None,
    quote_names: bool = True,
    video_only: bool = False,
) -> None:
    def q(val: str) -> str:
        return val.replace('"', r'\"')

    if quote_names:
        input_param = f'video="{q(video_name)}"'
        if audio_name and not video_only:
            input_param += f':audio="{q(audio_name)}"'
    else:
        input_param = f'video={video_name}'
        if audio_name and not video_only:
            input_param += f':audio={audio_name}'

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-thread_queue_size",
        _STREAM_THREAD_QUEUE_SIZE,
        "-f",
        "dshow",
        "-rtbufsize",
        _STREAM_DSHOW_RTBUF,
    ]
    if extra_input_args:
        cmd.extend(extra_input_args)
    cmd.extend(["-i", input_param])
    include_audio = bool(audio_name and not video_only)
    cmd.extend(_encoding_output_args(include_audio=include_audio))
    cmd.extend(_hls_output_args(hls_dir)[2])

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
        creationflags=creationflags,
    )

    state.ffmpeg = proc
    state.ffmpeg_cmd = cmd
    _start_ffmpeg_stderr_pump(state, proc)
    _wait_m3u8_or_fail(state, proc, m3u8_path)


def _start_ffmpeg_from_opencv_pipe(
    ffmpeg_path: str,
    state: StreamState,
    m3u8_path: Path,
    hls_dir: Path,
    cam_index: int,
    audio_name: Optional[str],
) -> None:
    if cv2 is None:
        raise RuntimeError("OpenCV недоступен.")

    backends = []
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_DSHOW", 0))
    with suppress(Exception):
        backends.append(getattr(cv2, "CAP_MSMF", 0))
    backends.append(getattr(cv2, "CAP_ANY", 0))

    cap = None
    for be in backends:
        with suppress(Exception):
            cap = cv2.VideoCapture(cam_index, be)
            if cap and cap.isOpened():
                break
    if not cap or not cap.isOpened():
        with suppress(Exception):
            if cap:
                cap.release()
        raise RuntimeError("Не удалось открыть камеру через OpenCV.")

    w = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_WIDTH", 3)) or 0) or 1280
    h = int(cap.get(getattr(cv2, "CAP_PROP_FRAME_HEIGHT", 4)) or 0) or 720
    fps = float(cap.get(getattr(cv2, "CAP_PROP_FPS", 5)) or 0.0)
    if fps <= 1 or fps > 120:
        fps = 30.0

    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-thread_queue_size",
        _STREAM_THREAD_QUEUE_SIZE,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{w}x{h}",
        "-r",
        f"{fps:.2f}",
        "-i",
        "pipe:0",
    ]

    if audio_name:
        def q(val: str) -> str:
            return val.replace('"', r'\"')
        cmd.extend([
            "-f",
            "dshow",
            "-rtbufsize",
            _STREAM_AUDIO_RTBUF,
            "-i",
            f'audio="{q(audio_name)}"',
        ])

    cmd.extend(["-map", "0:v:0"])
    if audio_name:
        cmd.extend(["-map", "1:a:0"])
    else:
        cmd.extend(["-an"])

    cmd.extend(_encoding_output_args(include_audio=bool(audio_name)))
    cmd.extend(_hls_output_args(hls_dir)[2])

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
        text=False,
        creationflags=creationflags,
    )

    state.ffmpeg = proc
    state.ffmpeg_cmd = cmd
    state.opencv_capture = cap
    state.opencv_stop = threading.Event()

    _start_ffmpeg_stderr_pump(state, proc)

    def _writer() -> None:
        frame_interval = 1.0 / max(1.0, float(fps))
        next_frame_deadline = time.perf_counter()
        try:
            while state.opencv_stop and not state.opencv_stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                try:
                    if proc.stdin:
                        proc.stdin.write(frame.tobytes())
                except Exception:
                    break
                next_frame_deadline += frame_interval
                now = time.perf_counter()
                delay = next_frame_deadline - now
                if delay > 0:
                    time.sleep(min(delay, frame_interval))
                elif delay < -(frame_interval * 2):
                    next_frame_deadline = now
        finally:
            with suppress(Exception):
                if proc.stdin:
                    proc.stdin.close()
            with suppress(Exception):
                cap.release()

    t = threading.Thread(target=_writer, daemon=True)
    state.opencv_thread = t
    t.start()

    _wait_m3u8_or_fail(state, proc, m3u8_path)


def _start_ffmpeg_stderr_pump(state: StreamState, proc: subprocess.Popen) -> None:
    state.ffmpeg_stderr_stop = threading.Event()

    def _pump() -> None:
        try:
            while proc.stderr:
                if state.ffmpeg_stderr_stop and state.ffmpeg_stderr_stop.is_set():
                    break
                try:
                    line = proc.stderr.readline()
                except Exception:
                    break
                if not line:
                    break
                text = _decode_best_effort(line if isinstance(line, (bytes, bytearray)) else str(line).encode())
                text = text.strip()
                if text:
                    state.ffmpeg_stderr_tail.append(text)
        except Exception:
            pass

    t = threading.Thread(target=_pump, daemon=True)
    state.ffmpeg_stderr_thread = t
    t.start()


def _wait_m3u8_or_fail(state: StreamState, proc: subprocess.Popen, m3u8_path: Path, timeout_s: int = 20) -> None:
    started = time.time()
    while (time.time() - started) < timeout_s:
        if proc.poll() is not None:
            tail = "\n".join(list(state.ffmpeg_stderr_tail)[-40:])
            raise RuntimeError(tail or "ffmpeg завершился во время запуска")
        if m3u8_path.is_file() and m3u8_path.stat().st_size > 0:
            return
        time.sleep(0.2)
    tail = "\n".join(list(state.ffmpeg_stderr_tail)[-40:])
    raise RuntimeError(
        "ffmpeg не успел создать плейлист (stream.m3u8)."
        + (f"\nПоследние строки ffmpeg:\n{tail}" if tail else "")
    )


def _stop_ffmpeg(state: StreamState) -> None:
    if state.opencv_stop:
        with suppress(Exception):
            state.opencv_stop.set()
    if state.opencv_thread and state.opencv_thread.is_alive():
        with suppress(Exception):
            state.opencv_thread.join(timeout=2)
    state.opencv_thread = None
    state.opencv_stop = None
    with suppress(Exception):
        if state.opencv_capture is not None:
            state.opencv_capture.release()
    state.opencv_capture = None

    if state.ffmpeg_stderr_stop:
        with suppress(Exception):
            state.ffmpeg_stderr_stop.set()
    if state.ffmpeg_stderr_thread and state.ffmpeg_stderr_thread.is_alive():
        with suppress(Exception):
            state.ffmpeg_stderr_thread.join(timeout=1)
    state.ffmpeg_stderr_thread = None
    state.ffmpeg_stderr_stop = None

    proc = state.ffmpeg
    state.ffmpeg = None
    if not proc:
        return
    with suppress(Exception):
        proc.terminate()
    with suppress(Exception):
        proc.wait(timeout=3)
    if proc.poll() is None:
        with suppress(Exception):
            proc.kill()


def list_devices(base_dir: str) -> dict:
    ffmpeg_path = resolve_ffmpeg_path(base_dir)
    videos: List[DeviceInfo] = []
    audios: List[DeviceInfo] = []
    has_dshow = False
    if ffmpeg_path:
        has_dshow = _ffmpeg_has_dshow(ffmpeg_path)
        if has_dshow:
            videos, audios = _probe_dshow_devices(ffmpeg_path)
    if not videos:
        videos = _probe_opencv_cameras()
    if not audios and has_dshow:
        audios.append(
            DeviceInfo(
                name="default",
                label="Системный микрофон (по умолчанию, dshow: default)",
                source="dshow",
            )
        )
    return {
        "ffmpeg_found": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path or "",
        "ffmpeg_has_dshow": has_dshow,
        "videos": [d.__dict__ for d in videos],
        "audios": [d.__dict__ for d in audios],
    }


def get_status() -> dict:
    with _STATE_LOCK:
        running = bool(_STATE.running and _STATE.ffmpeg and _STATE.ffmpeg.poll() is None)
        if _STATE.running and not running:
            _STATE.running = False
            _STATE.last_error = _STATE.last_error or "ffmpeg остановился"
        return {
            "running": running,
            "started_at": _STATE.started_at,
            "last_error": _STATE.last_error,
            "camera": _STATE.camera.__dict__ if _STATE.camera else None,
            "audio": _STATE.audio.__dict__ if _STATE.audio else None,
        }


def _show_control_window_for_web_stream(
    camera: Optional[DeviceInfo],
    audio: Optional[DeviceInfo],
) -> None:
    camera_label = camera.label if camera else "не выбрана"
    audio_label = audio.label if audio else "не выбран"
    details = (
        "Источник: веб-панель\n"
        f"Камера: {camera_label}\n"
        f"Микрофон: {audio_label}"
    )
    show_stream_control_window(
        owner_id=_WEB_STREAM_WINDOW_OWNER,
        title="Идет прямая трансляция",
        details=details,
        stop_callback=stop_stream,
    )

def start_stream(
    base_dir: str,
    video_name: str,
    audio_name: Optional[str] = None,
    video_alt: Optional[str] = None,
) -> dict:
    if not video_name:
        return {"ok": False, "stdout": "", "stderr": "Не выбрана камера."}
    with _STATE_LOCK:
        if _STATE.running and _STATE.ffmpeg and _STATE.ffmpeg.poll() is None:
            return {"ok": False, "stdout": "", "stderr": "Трансляция уже запущена."}
        _STATE.last_error = ""
        _STATE.ffmpeg_stderr_tail.clear()

    ffmpeg_path = resolve_ffmpeg_path(base_dir)
    if not ffmpeg_path:
        with _STATE_LOCK:
            _STATE.last_error = "ffmpeg.exe не найден"
        return {"ok": False, "stdout": "", "stderr": "ffmpeg.exe не найден."}

    source, clean_name = _parse_device_value(video_name)
    alt_source, clean_alt = _parse_device_value(video_alt or "")
    if alt_source != "dshow":
        clean_alt = ""
    hls_dir = _safe_hls_dir(base_dir)
    m3u8_path, _, _ = _hls_output_args(hls_dir)

    if source == "opencv":
        if cv2 is None:
            return {"ok": False, "stdout": "", "stderr": "OpenCV недоступен."}
        try:
            cam_index = int(clean_name)
        except Exception:
            return {"ok": False, "stdout": "", "stderr": "Некорректный индекс камеры."}
        try:
            _start_ffmpeg_from_opencv_pipe(
                ffmpeg_path=ffmpeg_path,
                state=_STATE,
                m3u8_path=m3u8_path,
                hls_dir=hls_dir,
                cam_index=cam_index,
                audio_name=audio_name,
            )
        except Exception as exc:
            _stop_ffmpeg(_STATE)
            _cleanup_hls(hls_dir)
            with _STATE_LOCK:
                _STATE.hls_dir = None
                _STATE.running = False
                _STATE.last_error = str(exc)
            return {"ok": False, "stdout": "", "stderr": str(exc)}
    else:
        variants: List[List[str]] = [
            ["-framerate", "25", "-video_size", "1280x720"],
            ["-framerate", "30", "-video_size", "1280x720"],
            ["-framerate", "25", "-video_size", "960x540"],
            ["-framerate", "25", "-video_size", "640x480"],
            [],
        ]
        name_candidates: List[str] = []
        try:
            dshow_videos, _dshow_audio = _probe_dshow_devices(ffmpeg_path)
        except Exception:
            dshow_videos = []
        matched_alt = ""
        matched_name = ""
        for dev in dshow_videos:
            if clean_name and (dev.name == clean_name or dev.alt_name == clean_name):
                matched_name = dev.name
                matched_alt = dev.alt_name or ""
                break
        if matched_name:
            name_candidates.append(matched_name)
            if matched_alt and matched_alt != matched_name:
                name_candidates.append(matched_alt)
        else:
            if clean_name:
                name_candidates.append(clean_name)
            if clean_alt and clean_alt != clean_name:
                name_candidates.append(clean_alt)

        last_error: Optional[str] = None
        audio_variants: List[Optional[str]] = []
        if audio_name:
            audio_variants.append(audio_name)
        audio_variants.append(None)

        for name in name_candidates:
            for extra in variants:
                for quote_names in (True, False):
                    for audio_candidate in audio_variants:
                        try:
                            _start_ffmpeg_dshow(
                                ffmpeg_path=ffmpeg_path,
                                state=_STATE,
                                m3u8_path=m3u8_path,
                                hls_dir=hls_dir,
                                video_name=name,
                                audio_name=audio_candidate,
                                extra_input_args=extra,
                                quote_names=quote_names,
                                video_only=audio_candidate is None,
                            )
                            with _STATE_LOCK:
                                _STATE.hls_dir = hls_dir
                                _STATE.running = True
                                _STATE.started_at = time.time()
                                _STATE.camera = DeviceInfo(name=name, label=name, source="dshow")
                                _STATE.audio = (
                                    DeviceInfo(name=audio_candidate, label=audio_candidate, source="dshow")
                                    if audio_candidate
                                    else None
                                )
                                _STATE.ffmpeg_stderr_tail.clear()
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = str(exc)
                            _stop_ffmpeg(_STATE)
                    if last_error is None:
                        break
                if last_error is None:
                    break
            if last_error is None:
                break

        if last_error:
            _cleanup_hls(hls_dir)
            with _STATE_LOCK:
                _STATE.hls_dir = None
                _STATE.running = False
                _STATE.last_error = last_error
            return {"ok": False, "stdout": "", "stderr": last_error}
    with _STATE_LOCK:
        if source == "opencv":
            _STATE.hls_dir = hls_dir
            _STATE.running = True
            _STATE.started_at = time.time()
            _STATE.camera = DeviceInfo(name=clean_name, label=clean_name, source="opencv")
            _STATE.audio = (
                DeviceInfo(name=audio_name, label=audio_name, source="dshow") if audio_name else None
            )
        camera = _STATE.camera
        audio = _STATE.audio

    _show_control_window_for_web_stream(camera, audio)

    return {"ok": True, "stdout": "Трансляция запущена.", "stderr": ""}


def stop_stream() -> dict:
    with _STATE_LOCK:
        if not _STATE.running:
            close_stream_control_window(_WEB_STREAM_WINDOW_OWNER)
            return {"ok": True, "stdout": "Трансляция уже остановлена.", "stderr": ""}
        _STATE.running = False
    try:
        _stop_ffmpeg(_STATE)
        with _STATE_LOCK:
            hls_dir = _STATE.hls_dir
            _STATE.hls_dir = None
            _STATE.ffmpeg_cmd = None
            _STATE.camera = None
            _STATE.audio = None
        _cleanup_hls(hls_dir)
        return {"ok": True, "stdout": "Трансляция остановлена.", "stderr": ""}
    finally:
        close_stream_control_window(_WEB_STREAM_WINDOW_OWNER)


def get_hls_dir() -> Optional[Path]:
    with _STATE_LOCK:
        return _STATE.hls_dir

