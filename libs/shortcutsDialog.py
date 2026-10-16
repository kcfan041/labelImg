#!/usr/bin/env python
# -*- coding: utf8 -*-
"""Keyboard shortcut editor: one row per command, up to MAX_KEYS keys each.

Click a key cell and press the new key (Esc cancels). A key already used by another command
is offered to be moved over, since Qt ignores a key bound to two commands at once.
Shortcuts are stored as Qt's portable text ('Ctrl+Z' means Cmd+Z on macOS).
"""
import sys

try:
    from PyQt5.QtCore import Qt, pyqtSignal
    from PyQt5.QtGui import QKeySequence
    from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
                                 QPushButton, QDialogButtonBox, QHeaderView, QMessageBox, QLabel,
                                 QLineEdit, QAbstractItemView)
except ImportError:
    from PyQt4.QtCore import Qt, pyqtSignal
    from PyQt4.QtGui import (QKeySequence, QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QPushButton, QDialogButtonBox, QHeaderView,
                             QMessageBox, QLabel, QLineEdit, QAbstractItemView)

from libs.physical_shortcuts import PhysicalKeyShortcuts

MAX_KEYS = 3

_MODIFIER_KEYS = (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr,
                  Qt.Key_CapsLock, Qt.Key_unknown)
_MODIFIER_MASK = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier


def normalize(seq):
    """Portable text of a single key sequence ('' when empty)."""
    return QKeySequence(seq).toString(QKeySequence.PortableText)


def native(seq):
    """How the key is written on this OS (⌘Z on macOS, Ctrl+Z on Windows)."""
    return QKeySequence(seq).toString(QKeySequence.NativeText)


class KeyCaptureButton(QPushButton):
    """Shows one key; click, then press a key to record it."""
    captured = pyqtSignal(str)

    def __init__(self, parent=None):
        super(KeyCaptureButton, self).__init__(parent)
        self.key = ''
        self.recording = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self.start)
        self.refresh()

    def set_key(self, key):
        self.key = key
        self.refresh()

    def refresh(self):
        if self.recording:
            self.setText('Press a key… (Esc: cancel)')
        else:
            self.setText(native(self.key) if self.key else '—')

    def start(self):
        self.recording = True
        self.grabKeyboard()
        self.refresh()

    def stop(self):
        if self.recording:
            self.recording = False
            self.releaseKeyboard()
            self.refresh()

    def focusOutEvent(self, event):
        self.stop()
        super(KeyCaptureButton, self).focusOutEvent(event)

    def event(self, event):
        # Let Tab / Shift+Tab be recorded too instead of moving the focus
        if self.recording and event.type() == event.KeyPress:
            self.keyPressEvent(event)
            return True
        if self.recording and event.type() == event.ShortcutOverride:
            event.accept()  # no menu shortcut fires while recording
            return True
        return super(KeyCaptureButton, self).event(event)

    def keyPressEvent(self, event):
        if not self.recording:
            return super(KeyCaptureButton, self).keyPressEvent(event)
        key = event.key()
        if key in _MODIFIER_KEYS:
            return  # wait for the real key
        mods = int(event.modifiers() & _MODIFIER_MASK)
        if key == Qt.Key_Escape and not mods:
            self.stop()
            return
        # With Zhuyin / Pinyin active the key arrives as a non-Latin symbol: use the physical key
        if key > 0xff or key == 0:
            physical = PhysicalKeyShortcuts.physical_key(event)
            if physical is not None:
                key = physical
        if key == Qt.Key_Backtab:
            key = Qt.Key_Tab
        self.stop()
        seq = normalize(QKeySequence(mods | key))
        if seq:
            self.captured.emit(seq)


class ShortcutsDialog(QDialog):
    """entries: list of (id, name, default keys); current: {id: [keys]}."""

    def __init__(self, entries, current, parent=None):
        super(ShortcutsDialog, self).__init__(parent)
        self.setWindowTitle('Keyboard Shortcuts')
        self.entries = entries
        self.defaults = dict((e[0], list(e[2])) for e in entries)
        self.keys = dict((e[0], list(current.get(e[0], e[2]))) for e in entries)
        self.buttons = {}

        self.search = QLineEdit()
        self.search.setPlaceholderText('Search commands or keys…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_search)

        headers = ['Command'] + ['Key %d' % (i + 1) for i in range(MAX_KEYS)] + ['', '']
        self.table = QTableWidget(len(entries), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        for row, (eid, name, _default) in enumerate(entries):
            item = QTableWidgetItem(name)
            self.table.setItem(row, 0, item)
            cells = []
            for col in range(MAX_KEYS):
                button = KeyCaptureButton()
                button.captured.connect(lambda seq, eid=eid, col=col: self.assign(eid, col, seq))
                self.table.setCellWidget(row, 1 + col, button)
                cells.append(button)
            self.buttons[eid] = cells
            clear = QPushButton('Clear')
            clear.setToolTip('Remove every key of this command')
            clear.clicked.connect(lambda _=False, eid=eid: self.set_keys(eid, []))
            self.table.setCellWidget(row, 1 + MAX_KEYS, clear)
            reset = QPushButton('Default')
            reset.setToolTip('Default: %s' % (', '.join(native(k) for k in self.defaults[eid]) or 'none'))
            reset.clicked.connect(lambda _=False, eid=eid: self.reset(eid))
            self.table.setCellWidget(row, 2 + MAX_KEYS, reset)
            self.refresh_row(eid)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in range(1, 1 + MAX_KEYS):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, 190)

        hint = QLabel('Click a key cell, then press the new key. Esc cancels. '
                      'Changes apply right away after OK.')
        hint.setWordWrap(True)

        reset_all = QPushButton('Restore All Defaults')
        reset_all.clicked.connect(self.reset_all)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(reset_all)
        bottom.addStretch(1)
        bottom.addWidget(box)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        layout.addLayout(bottom)
        self.resize(1000, 640)

    def name_of(self, eid):
        for e in self.entries:
            if e[0] == eid:
                return e[1]
        return eid

    def refresh_row(self, eid):
        keys = self.keys[eid]
        for col, button in enumerate(self.buttons[eid]):
            button.set_key(keys[col] if col < len(keys) else '')
        row = [e[0] for e in self.entries].index(eid)
        item = self.table.item(row, 0)
        font = item.font()
        font.setBold(keys != self.defaults[eid])  # changed from the default
        item.setFont(font)

    def set_keys(self, eid, keys):
        self.keys[eid] = keys
        self.refresh_row(eid)

    def owner_of(self, seq, skip=None):
        for eid, keys in self.keys.items():
            if eid != skip and seq in keys:
                return eid
        return None

    def assign(self, eid, col, seq):
        keys = list(self.keys[eid])
        if seq in keys:
            return  # already one of this command's keys
        owner = self.owner_of(seq, skip=eid)
        if owner is not None:
            answer = QMessageBox.question(
                self, 'Key already used',
                '%s is already used by "%s".\n\nMove it to "%s"?'
                % (native(seq), self.name_of(owner), self.name_of(eid)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.set_keys(owner, [k for k in self.keys[owner] if k != seq])
        if col < len(keys):
            keys[col] = seq
        else:
            keys.append(seq)
        self.set_keys(eid, keys[:MAX_KEYS])

    def reset(self, eid):
        for seq in self.defaults[eid]:
            owner = self.owner_of(seq, skip=eid)
            if owner is not None:
                answer = QMessageBox.question(
                    self, 'Key already used',
                    '%s (default of "%s") is now used by "%s".\n\nMove it back?'
                    % (native(seq), self.name_of(eid), self.name_of(owner)),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                self.set_keys(owner, [k for k in self.keys[owner] if k != seq])
        self.set_keys(eid, list(self.defaults[eid]))

    def reset_all(self):
        for eid in self.keys:
            self.set_keys(eid, list(self.defaults[eid]))

    def apply_search(self, text):
        text = text.strip().lower()
        for row, (eid, name, _default) in enumerate(self.entries):
            hay = ' '.join([name] + [native(k) for k in self.keys[eid]] + self.keys[eid]).lower()
            self.table.setRowHidden(row, bool(text) and text not in hay)

    def result_keys(self):
        """Only the commands that differ from the default."""
        return dict((eid, keys) for eid, keys in self.keys.items() if keys != self.defaults[eid])
