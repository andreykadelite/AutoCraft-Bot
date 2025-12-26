# -*- coding: utf-8 -*-
"""
Окно «Справка» вынесено сюда, чтобы gui.py не тащил весь UI справки при старте.
Импортируется лениво: только при нажатии кнопки «Справка».
"""

from __future__ import annotations

import info

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QTextBrowser,
    QDialogButtonBox,
    QFrame,
    QTextEdit,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QTextCursor


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("helpDialog")
        self.setWindowTitle("Справка")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setAccessibleName("Окно справки")
        self.setAccessibleDescription("Описание возможностей программы и контакты поддержки")
        self.setMinimumSize(560, 420)
        self.setSizeGripEnabled(True)

        # Подтягиваем палитру/стили родителя, чтобы окно не выглядело «чужим».
        if parent:
            try:
                self.setPalette(parent.palette())
                if parent.styleSheet():
                    self.setStyleSheet(parent.styleSheet())
            except Exception:
                pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        header = QLabel("Справка")
        header.setObjectName("helpDialogHeader")
        header.setAccessibleName("Заголовок справки")
        header.setAccessibleDescription("Заголовок окна справки")
        header.setAlignment(Qt.AlignLeft)
        header.setStyleSheet("font-size: 16pt; font-weight: 600;")
        layout.addWidget(header)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setAccessibleName("Разделитель")
        layout.addWidget(divider)

        self.text_view = QTextBrowser()
        self.text_view.setObjectName("helpDialogText")
        self.text_view.setReadOnly(True)

        help_text = (info.CONTACT_TEXT or "").strip() + "\n\n" + (info.HELP_TEXT or "").strip()

        set_markdown = getattr(self.text_view, "setMarkdown", None)
        if callable(set_markdown):
            try:
                self.text_view.setMarkdown(help_text)
            except Exception:
                self.text_view.setPlainText(help_text)
        else:
            self.text_view.setPlainText(help_text)

        # Важно для скринридеров: виджет должен быть "похож" на обычный текстовый редактор
        # (даже если он readOnly), чтобы стрелки корректно двигали каретку, а не просто скроллили.
        self.text_view.setAccessibleName("Текст справки")
        self.text_view.setAccessibleDescription("Подробная справка и контакты поддержки")
        self.text_view.setFocusPolicy(Qt.StrongFocus)
        self.text_view.setTabChangesFocus(True)
        self.text_view.setFrameShape(QFrame.NoFrame)
        self.text_view.setTextInteractionFlags(
            Qt.TextEditorInteraction
            | Qt.LinksAccessibleByKeyboard
            | Qt.LinksAccessibleByMouse
        )
        self.text_view.setOpenExternalLinks(True)
        self.text_view.setLineWrapMode(QTextEdit.WidgetWidth)
        try:
            self.text_view.document().setDefaultFont(QFont("Segoe UI", 10))
        except Exception:
            pass

        layout.addWidget(self.text_view)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn = button_box.button(QDialogButtonBox.Close)
        close_btn.setText("Закрыть")
        close_btn.setAccessibleName("Закрыть справку")
        close_btn.setAccessibleDescription("Закрывает окно справки")
        close_btn.setDefault(True)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setTabOrder(self.text_view, close_btn)

        # Фокус/каретка задаются после показа окна (иначе Qt может перекинуть фокус на кнопку).
        # Это критично для NVDA/JAWS/VoiceOver: тогда чтение стрелками начинает работать сразу.
        self._deferred_focus_scheduled = False

    def _focus_text_at_start(self):
        try:
            cursor = self.text_view.textCursor()
            cursor.setPosition(0)
            self.text_view.setTextCursor(cursor)
            self.text_view.ensureCursorVisible()
            self.text_view.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_deferred_focus_scheduled", False):
            self._deferred_focus_scheduled = True
            QTimer.singleShot(0, self._focus_text_at_start)


def show_help_dialog(parent=None):
    """Открыть справку (модально)."""
    dlg = HelpDialog(parent)
    dlg.exec_()
