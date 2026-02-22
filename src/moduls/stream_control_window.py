from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from qasync import QEventLoop  # type: ignore

    _HAS_QASYNC = True
except Exception:
    _HAS_QASYNC = False

    class QEventLoop(asyncio.AbstractEventLoop):  # type: ignore
        pass

try:
    from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal, pyqtSlot
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

    _HAS_QT = True
except Exception:
    _HAS_QT = False


_LOGGER = logging.getLogger("stream_control_window")
_LOCK = threading.Lock()
_WINDOWS: Dict[str, "StreamControlWindow"] = {}
_BUS: Optional["_WindowBus"] = None
_BRIDGE: Optional["_WindowBridge"] = None

StopCallback = Callable[[], Any]

_DEFAULT_ALERT_TEXT = "ВНИМАНИЕ: ИДЕТ ПРЯМАЯ ТРАНСЛЯЦИЯ С ВЕБ-КАМЕРЫ."
_DEFAULT_TITLE_TEXT = "Прямая трансляция активна"
_DEFAULT_DETAILS_TEXT = "Программа: AutoCraft-Bot. Источник видеопотока: веб-камера."
_DEFAULT_STATUS_IDLE = "Для завершения трансляции нажмите кнопку «Завершить трансляцию»."
_DEFAULT_STATUS_STOPPING = "Выполняется корректное завершение трансляции. Пожалуйста, подождите."


def _normalize_stop_result(result: Any) -> Tuple[bool, str]:
    if isinstance(result, dict):
        ok = bool(result.get("ok", True))
        err = str(result.get("stderr") or result.get("error") or "").strip()
        if not ok and not err:
            err = "Не удалось завершить трансляцию."
        return ok, err
    if isinstance(result, bool):
        return result, "" if result else "Не удалось завершить трансляцию."
    return True, ""


async def _call_stop_callback_async(callback: StopCallback) -> Tuple[bool, str]:
    try:
        if inspect.iscoroutinefunction(callback):
            result = await callback()
        else:
            result = await asyncio.to_thread(callback)
            if inspect.isawaitable(result):
                result = await result
        return _normalize_stop_result(result)
    except Exception as exc:
        return False, str(exc)


def _call_stop_callback_sync(callback: StopCallback) -> Tuple[bool, str]:
    try:
        result = callback()
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        return _normalize_stop_result(result)
    except Exception as exc:
        return False, str(exc)


def _running_qasync_loop() -> Optional[asyncio.AbstractEventLoop]:
    if not _HAS_QASYNC:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if isinstance(loop, QEventLoop):
        return loop
    return None


if _HAS_QT:
    class _StopResultSignals(QObject):
        finished = pyqtSignal(bool, str)


    class StreamControlWindow(QWidget):
        def __init__(
            self,
            owner_id: str,
            title: str,
            details: str,
            stop_callback: StopCallback,
            on_closed: Callable[[str], None],
        ) -> None:
            super().__init__()
            self._owner_id = owner_id
            self._stop_callback = stop_callback
            self._on_closed = on_closed
            self._allow_close = False
            self._stopping = False
            self._signals = _StopResultSignals()
            self._signals.finished.connect(self._handle_stop_finished, type=Qt.QueuedConnection)
            self._build_ui(title=title, details=details)

        def _build_ui(self, title: str, details: str) -> None:
            self.setObjectName("streamControlRoot")
            self.setWindowTitle("AutoCraft-Bot | Контроль прямой трансляции")
            self.setWindowFlags(
                Qt.Window
                | Qt.CustomizeWindowHint
                | Qt.WindowTitleHint
                | Qt.WindowStaysOnTopHint
            )
            self.setWindowModality(Qt.NonModal)
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
            self.setWindowFlag(Qt.WindowMinimizeButtonHint, False)
            self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
            self.setMinimumSize(460, 250)
            self.setMaximumSize(560, 360)
            self.setFocusPolicy(Qt.StrongFocus)
            self.setAccessibleName("Окно контроля прямой трансляции AutoCraft-Bot")
            self.setAccessibleDescription(
                "Внимание: выполняется прямая трансляция с веб-камеры. "
                "Окно нельзя закрыть или свернуть стандартными кнопками. "
                "Для завершения используйте кнопку «Завершить трансляцию» или сочетание Alt+S."
            )

            layout = QVBoxLayout(self)
            layout.setContentsMargins(24, 22, 24, 20)
            layout.setSpacing(12)

            self._alert_label = QLabel(_DEFAULT_ALERT_TEXT)
            self._alert_label.setObjectName("streamAlertLabel")
            self._alert_label.setWordWrap(True)
            self._alert_label.setAccessibleName("Предупреждение о прямой трансляции")
            self._alert_label.setAccessibleDescription(
                "Критически важное уведомление: идет прямая трансляция с веб-камеры."
            )
            alert_font = QFont()
            alert_font.setBold(True)
            alert_font.setPointSize(11)
            self._alert_label.setFont(alert_font)

            self._state_label = QLabel("Статус: трансляция активна")
            self._state_label.setObjectName("streamStateLabel")
            self._state_label.setAccessibleName("Состояние трансляции")
            self._state_label.setAccessibleDescription("Показывает, что передача видео выполняется в реальном времени.")
            state_font = QFont()
            state_font.setBold(True)
            state_font.setPointSize(11)
            self._state_label.setFont(state_font)

            self._title_label = QLabel(title or _DEFAULT_TITLE_TEXT)
            self._title_label.setObjectName("streamTitleLabel")
            self._title_label.setWordWrap(True)
            self._title_label.setAccessibleName("Заголовок трансляции")
            self._title_label.setAccessibleDescription("Краткое описание текущей трансляции.")
            title_font = QFont()
            title_font.setBold(True)
            title_font.setPointSize(14)
            self._title_label.setFont(title_font)

            self._details_label = QLabel(details or _DEFAULT_DETAILS_TEXT)
            self._details_label.setObjectName("streamDetailsLabel")
            self._details_label.setWordWrap(True)
            self._details_label.setAccessibleName("Служебная информация о трансляции")
            self._details_label.setAccessibleDescription(
                "Содержит название программы, источник видео и другие параметры сеанса."
            )

            self._status_label = QLabel(_DEFAULT_STATUS_IDLE)
            self._status_label.setObjectName("streamStatusLabel")
            self._status_label.setWordWrap(True)
            self._status_label.setAccessibleName("Статус выполнения операции")
            self._status_label.setAccessibleDescription(
                "Показывает ход завершения трансляции и сообщения об ошибках."
            )

            self._stop_button = QPushButton("Завершить трансляцию")
            self._stop_button.setObjectName("streamStopButton")
            self._stop_button.setDefault(True)
            self._stop_button.setAutoDefault(True)
            self._stop_button.setShortcut("Alt+S")
            self._stop_button.setAccessibleName("Завершить трансляцию")
            self._stop_button.setAccessibleDescription(
                "Единственная доступная кнопка. Завершает трансляцию и закрывает окно. Горячая клавиша Alt+S."
            )
            self._stop_button.setToolTip("Завершить трансляцию (Alt+S)")
            self._stop_button.clicked.connect(self._handle_stop_clicked)

            layout.addWidget(self._alert_label)
            layout.addWidget(self._state_label)
            layout.addWidget(self._title_label)
            layout.addWidget(self._details_label)
            layout.addWidget(self._status_label)
            layout.addStretch(1)
            layout.addWidget(self._stop_button)

            self.setStyleSheet(
                """
                QWidget#streamControlRoot {
                    background: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 1,
                        stop: 0 #0f2336,
                        stop: 1 #1c3550
                    );
                    color: #eaf3ff;
                    border: 1px solid #2b4d6f;
                    border-radius: 14px;
                }
                QLabel#streamAlertLabel {
                    color: #ffd38a;
                }
                QLabel#streamStateLabel {
                    color: #7ef0a6;
                }
                QLabel#streamTitleLabel {
                    color: #ffffff;
                }
                QLabel#streamDetailsLabel {
                    color: #d3e5ff;
                }
                QLabel#streamStatusLabel {
                    color: #b8d4f7;
                }
                QPushButton#streamStopButton {
                    min-height: 42px;
                    font-weight: 700;
                    border-radius: 10px;
                    background-color: #d64045;
                    color: #ffffff;
                    border: 1px solid #ff8c8f;
                }
                QPushButton#streamStopButton:hover {
                    background-color: #ec4d52;
                }
                QPushButton#streamStopButton:pressed {
                    background-color: #bf3539;
                }
                QPushButton#streamStopButton:disabled {
                    background-color: #6a7281;
                    color: #dbe1eb;
                    border-color: #8892a4;
                }
                """
            )

        def update_payload(self, title: str, details: str, stop_callback: StopCallback) -> None:
            self._stop_callback = stop_callback
            self._alert_label.setText(_DEFAULT_ALERT_TEXT)
            self._title_label.setText(title or _DEFAULT_TITLE_TEXT)
            self._details_label.setText(details or _DEFAULT_DETAILS_TEXT)
            if not self._stopping:
                self._status_label.setText(_DEFAULT_STATUS_IDLE)
                self._stop_button.setEnabled(True)
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self._stop_button.setFocus(Qt.TabFocusReason)

        def force_close(self) -> None:
            self._allow_close = True
            self.close()

        def _set_stopping_state(self, active: bool) -> None:
            self._stopping = active
            self._stop_button.setEnabled(not active)
            if active:
                self._status_label.setText(_DEFAULT_STATUS_STOPPING)

        @pyqtSlot()
        def _handle_stop_clicked(self) -> None:
            if self._stopping:
                return
            self._set_stopping_state(True)

            loop = _running_qasync_loop()
            if loop is not None:
                loop.create_task(self._stop_with_qasync())
                return

            self._stop_in_thread()

        async def _stop_with_qasync(self) -> None:
            ok, err = await _call_stop_callback_async(self._stop_callback)
            self._signals.finished.emit(ok, err)

        def _stop_in_thread(self) -> None:
            def _worker() -> None:
                ok, err = _call_stop_callback_sync(self._stop_callback)
                self._signals.finished.emit(ok, err)

            threading.Thread(
                target=_worker,
                daemon=True,
                name=f"stream-stop-{self._owner_id[:24]}",
            ).start()

        @pyqtSlot(bool, str)
        def _handle_stop_finished(self, ok: bool, err: str) -> None:
            if ok:
                self.force_close()
                return
            self._set_stopping_state(False)
            self._status_label.setText(
                f"Не удалось завершить трансляцию: {err}" if err else "Не удалось завершить трансляцию."
            )

        def showEvent(self, event) -> None:  # type: ignore[override]
            super().showEvent(event)
            self._stop_button.setFocus(Qt.TabFocusReason)

        def closeEvent(self, event) -> None:  # type: ignore[override]
            if not self._allow_close:
                event.ignore()
                self.showNormal()
                self.raise_()
                self.activateWindow()
                return
            try:
                self._on_closed(self._owner_id)
            except Exception:
                pass
            super().closeEvent(event)

        def changeEvent(self, event) -> None:  # type: ignore[override]
            if event.type() == QEvent.WindowStateChange and self.isMinimized():
                self.showNormal()
                self.raise_()
                self.activateWindow()
            super().changeEvent(event)

        def keyPressEvent(self, event) -> None:  # type: ignore[override]
            if event.key() == Qt.Key_Escape:
                event.ignore()
                return
            super().keyPressEvent(event)


    class _WindowBus(QObject):
        show_window = pyqtSignal(str, str, str, object)
        close_window = pyqtSignal(str)


    class _WindowBridge(QObject):
        @pyqtSlot(str, str, str, object)
        def do_show(
            self,
            owner_id: str,
            title: str,
            details: str,
            stop_callback: object,
        ) -> None:
            owner = (owner_id or "").strip() or "default"
            callback: StopCallback
            if callable(stop_callback):
                callback = stop_callback  # type: ignore[assignment]
            else:
                callback = lambda: {"ok": False, "stderr": "Не задан обработчик остановки."}

            with _LOCK:
                window = _WINDOWS.get(owner)
                if window is None:
                    window = StreamControlWindow(
                        owner_id=owner,
                        title=title,
                        details=details,
                        stop_callback=callback,
                        on_closed=_remove_window,
                    )
                    _WINDOWS[owner] = window
                else:
                    window.update_payload(title=title, details=details, stop_callback=callback)

            window.showNormal()
            window.raise_()
            window.activateWindow()

        @pyqtSlot(str)
        def do_close(self, owner_id: str) -> None:
            owner = (owner_id or "").strip()
            if owner:
                with _LOCK:
                    window = _WINDOWS.pop(owner, None)
                if window is not None:
                    window.force_close()
                return

            with _LOCK:
                items = list(_WINDOWS.items())
                _WINDOWS.clear()
            for _, window in items:
                window.force_close()


def _remove_window(owner_id: str) -> None:
    with _LOCK:
        _WINDOWS.pop(owner_id, None)


def _ensure_window_bridge() -> bool:
    if not _HAS_QT:
        return False
    app = QApplication.instance()
    if app is None:
        return False

    global _BUS, _BRIDGE
    with _LOCK:
        if _BRIDGE is None:
            _BRIDGE = _WindowBridge()
            _BRIDGE.moveToThread(app.thread())
        if _BUS is None:
            _BUS = _WindowBus()
            _BUS.moveToThread(app.thread())
            _BUS.show_window.connect(_BRIDGE.do_show, type=Qt.QueuedConnection)
            _BUS.close_window.connect(_BRIDGE.do_close, type=Qt.QueuedConnection)
    return True


def show_stream_control_window(
    owner_id: str,
    title: str,
    details: str,
    stop_callback: StopCallback,
) -> bool:
    if not callable(stop_callback):
        return False
    if not _ensure_window_bridge():
        return False
    try:
        assert _BUS is not None
        _BUS.show_window.emit(owner_id or "", title or "", details or "", stop_callback)
        return True
    except Exception as exc:
        _LOGGER.debug("stream window show failed: %s", exc)
        return False


def close_stream_control_window(owner_id: str = "") -> None:
    if not _ensure_window_bridge():
        return
    try:
        assert _BUS is not None
        _BUS.close_window.emit(owner_id or "")
    except Exception as exc:
        _LOGGER.debug("stream window close failed: %s", exc)

