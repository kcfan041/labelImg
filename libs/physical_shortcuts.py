#!/usr/bin/env python
# -*- coding: utf8 -*-
"""Make single-key shortcuts (e / w / s / a / d / x / space ...) work while a non-Latin
input method such as Zhuyin or Pinyin is active.

With such an input source the key event no longer carries the Latin letter
(e.g. 'w' arrives as a Bopomofo symbol), so Qt's shortcut matching fails. When
that happens this filter maps the physical key (the macOS / Windows virtual
key code, which doesn't depend on the input source) back to its Latin key and
triggers the matching QAction.
"""
import sys

try:
    from PyQt5.QtCore import QObject, QEvent, Qt, QTimer
    from PyQt5.QtGui import QKeySequence
    from PyQt5.QtWidgets import (QAction, QApplication, QAbstractSpinBox, QComboBox, QLineEdit,
                                 QPlainTextEdit, QTextEdit)
except ImportError:
    from PyQt4.QtCore import QObject, QEvent, Qt, QTimer
    from PyQt4.QtGui import (QKeySequence, QAction, QApplication, QAbstractSpinBox, QComboBox,
                             QLineEdit, QPlainTextEdit, QTextEdit)

_ARROW_KEYS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)
_TEXT_WIDGETS = (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)

# macOS kVK_ANSI_* virtual key codes (Carbon HIToolbox/Events.h)
_MAC_VIRTUAL_KEYS = {
    0x00: Qt.Key_A, 0x0B: Qt.Key_B, 0x08: Qt.Key_C, 0x02: Qt.Key_D, 0x0E: Qt.Key_E,
    0x03: Qt.Key_F, 0x05: Qt.Key_G, 0x04: Qt.Key_H, 0x22: Qt.Key_I, 0x26: Qt.Key_J,
    0x28: Qt.Key_K, 0x25: Qt.Key_L, 0x2E: Qt.Key_M, 0x2D: Qt.Key_N, 0x1F: Qt.Key_O,
    0x23: Qt.Key_P, 0x0C: Qt.Key_Q, 0x0F: Qt.Key_R, 0x01: Qt.Key_S, 0x11: Qt.Key_T,
    0x20: Qt.Key_U, 0x09: Qt.Key_V, 0x0D: Qt.Key_W, 0x07: Qt.Key_X, 0x10: Qt.Key_Y,
    0x06: Qt.Key_Z,
    0x1D: Qt.Key_0, 0x12: Qt.Key_1, 0x13: Qt.Key_2, 0x14: Qt.Key_3, 0x15: Qt.Key_4,
    0x17: Qt.Key_5, 0x16: Qt.Key_6, 0x1A: Qt.Key_7, 0x1C: Qt.Key_8, 0x19: Qt.Key_9,
    0x31: Qt.Key_Space,
}

# Ctrl/Cmd/Alt shortcuts already go through the Latin layout; only plain (or Shift) keys need help
_BLOCKING_MODIFIERS = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier


# Holding a key bound to a hold-repeat action (next / prev image): fire once on press,
# then after HOLD_DELAY_MS keep firing every REPEAT_INTERVAL_MS until the key is released
HOLD_DELAY_MS = 400
REPEAT_INTERVAL_MS = 150


class PhysicalKeyShortcuts(QObject):

    def __init__(self, window, hold_repeat_actions=()):
        super(PhysicalKeyShortcuts, self).__init__(window)
        self.window = window
        self.hold_repeat_actions = list(hold_repeat_actions)
        for action in self.hold_repeat_actions:
            action.setAutoRepeat(False)  # repeating is paced by repeat_timer instead of the OS key repeat
        self.held_action = None
        self.held_key = None
        self.repeat_timer = QTimer(self)
        self.repeat_timer.timeout.connect(self._repeat_held_action)

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.KeyRelease:
            if self.held_action is not None and not event.isAutoRepeat() and self._key(event) == self.held_key:
                self.stop_hold()
            return False
        if etype == QEvent.WindowDeactivate:
            self.stop_hold()
            return False
        if etype not in (QEvent.KeyPress, QEvent.ShortcutOverride):
            return False
        if QApplication.activeModalWidget() is not None:
            return False  # dialogs (statistics, shortcuts, label ...) keep their own keys
        focus = QApplication.focusWidget()
        if self._widget_wants_arrow(focus, event):
            if etype == QEvent.ShortcutOverride:
                event.accept()  # e.g. Up in a spin box changes its value instead of the class
            return False
        if focus is not None and focus.testAttribute(Qt.WA_InputMethodEnabled):
            return False  # typing into a text field (e.g. the label dialog) keeps the input method

        hold_action = self._hold_action(event)
        if hold_action is not None:
            if etype == QEvent.ShortcutOverride:
                # Claim the key so Qt doesn't fire the shortcut itself; it's handled on KeyPress below
                event.accept()
                return True
            if not event.isAutoRepeat() and hold_action is not self.held_action:
                self.held_action = hold_action
                self.held_key = self._key(event)
                hold_action.trigger()
                self.repeat_timer.start(HOLD_DELAY_MS)
            return True  # swallow the OS auto-repeat presses

        # Only key presses that Qt's shortcut system didn't already consume reach here
        if etype != QEvent.KeyPress or event.isAutoRepeat():
            return False
        if int(event.modifiers() & _BLOCKING_MODIFIERS):
            return False

        key = self.physical_key(event)
        if key is None or key == event.key():
            return False

        sequence = QKeySequence(int(event.modifiers() & Qt.ShiftModifier) | key)
        for action in self.window.findChildren(QAction):
            # Like Qt, ignore actions not placed on any toolbar/menu (e.g. the advanced-mode
            # "Create RectBox" that also uses 'e' while in beginner mode)
            if action.isEnabled() and action.associatedWidgets() and sequence in action.shortcuts():
                action.trigger()
                return True
        return False

    @staticmethod
    def _widget_wants_arrow(focus, event):
        """Arrow keys are image / class shortcuts, except in widgets that need them:
        text fields and spin boxes keep all four, combo boxes keep Up / Down."""
        key = event.key()
        if focus is None or key not in _ARROW_KEYS:
            return False
        if int(event.modifiers() & (_BLOCKING_MODIFIERS | Qt.ShiftModifier)):
            return False
        if isinstance(focus, _TEXT_WIDGETS):
            return True
        return isinstance(focus, QComboBox) and key in (Qt.Key_Up, Qt.Key_Down)

    def stop_hold(self):
        self.repeat_timer.stop()
        self.held_action = None
        self.held_key = None

    def _repeat_held_action(self):
        if self.held_action is None or not self.held_action.isEnabled():
            self.stop_hold()
            return
        self.repeat_timer.setInterval(REPEAT_INTERVAL_MS)
        if QApplication.activeModalWidget() is not None:
            return  # e.g. a "save changes?" prompt is open; don't stack more on top of it
        self.held_action.trigger()

    def _hold_action(self, event):
        if int(event.modifiers() & (_BLOCKING_MODIFIERS | Qt.ShiftModifier)):
            return None
        sequence = QKeySequence(self._key(event))
        for action in self.hold_repeat_actions:
            if action.isEnabled() and sequence in action.shortcuts():
                return action
        return None

    def _key(self, event):
        """Latin key for the event, from the physical key when an input method changed it."""
        return self.physical_key(event) or event.key()

    @staticmethod
    def physical_key(event):
        vk = event.nativeVirtualKey()
        if sys.platform == 'darwin':
            return _MAC_VIRTUAL_KEYS.get(vk)
        if sys.platform == 'win32':
            # Windows virtual-key codes for letters/digits/space equal their ASCII codes, as do Qt's keys
            if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39 or vk == 0x20:
                return vk
        return None
