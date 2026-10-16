#!/usr/bin/env python
# -*- coding: utf8 -*-
"""Ask which class list to use for datasets that have no classes.txt.

YOLO label files only store class ids, so a dataset without classes.txt can't
be read or saved safely until the user says which class list (and order) it
uses. Nothing is picked silently.
"""
import codecs
import os

try:
    from PyQt5.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                                 QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                                 QVBoxLayout)
except ImportError:
    from PyQt4.QtGui import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                             QVBoxLayout)

MODE_COPY, MODE_FILE, MODE_EMPTY = 'copy', 'file', 'empty'


def read_classes_file(path):
    """Class names from a classes.txt, indexed the same way YoloReader does. None if missing."""
    if not path or not os.path.isfile(path):
        return None
    with codecs.open(path, 'r', 'utf8') as f:
        content = f.read().strip('\n')
    return [line.strip() for line in content.split('\n')] if content else []


def write_classes_file(path, classes):
    with codecs.open(path, 'w', 'utf8') as f:
        for name in classes:
            f.write(name + '\n')


def count_label_files(labels_dir):
    """Number of YOLO label files (*.txt except classes.txt) under labels_dir."""
    count = 0
    for root, _dirs, files in os.walk(labels_dir):
        count += sum(1 for f in files if f.lower().endswith('.txt') and f != 'classes.txt')
    return count


class ClassesDialog(QDialog):
    """missing: [{'name', 'label_count'}]; sources: [{'name', 'classes'}] to copy from."""

    def __init__(self, missing, sources, start_dir='.', parent=None):
        super(ClassesDialog, self).__init__(parent)
        self.setWindowTitle('No classes.txt')
        self.sources = sources
        self.start_dir = start_dir
        self.chosen = None

        layout = QVBoxLayout(self)
        names = '\n'.join('  • %s' % m['name'] for m in missing)
        layout.addWidget(QLabel('These datasets have no classes.txt, so it is unknown which class '
                                'each id in the label files means:\n%s' % names))
        labelled = [m for m in missing if m['label_count']]
        if labelled:
            warning = QLabel('⚠ Already labelled: %s.\nPick the class list these labels were made with. '
                             'A list in a different order would relabel every box.'
                             % ', '.join('%s (%d files)' % (m['name'], m['label_count']) for m in labelled))
            warning.setStyleSheet('color: #b35900;')
            warning.setWordWrap(True)
            layout.addWidget(warning)

        self.group = QButtonGroup(self)

        # Copy from another dataset
        self.copy_radio = QRadioButton('Copy classes.txt from:')
        self.copy_combo = QComboBox()
        for source in sources:
            self.copy_combo.addItem('%s  (%d classes: %s)' % (source['name'], len(source['classes']),
                                                              ', '.join(source['classes'][:6]) +
                                                              (', …' if len(source['classes']) > 6 else '')))
        row = QHBoxLayout()
        row.addWidget(self.copy_radio)
        row.addWidget(self.copy_combo, 1)
        layout.addLayout(row)
        if not sources:
            self.copy_radio.setEnabled(False)
            self.copy_combo.setEnabled(False)
            self.copy_combo.addItem('(no other dataset has a classes.txt)')

        # Pick a file
        self.file_radio = QRadioButton('Use a classes file:')
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.file_radio)
        row.addWidget(self.file_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        # Start empty
        self.empty_radio = QRadioButton('Start with an empty class list (classes are added in the order you use them)')
        layout.addWidget(self.empty_radio)

        for radio in (self.copy_radio, self.file_radio, self.empty_radio):
            self.group.addButton(radio)
        if sources:
            self.copy_radio.setChecked(True)
        elif labelled:
            self.file_radio.setChecked(True)
        else:
            self.empty_radio.setChecked(True)

        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color: #c00;')
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a classes file', self.start_dir,
                                              'Text files (*.txt);;All files (*)')
        if path:
            self.file_edit.setText(path)
            self.file_radio.setChecked(True)

    def validate(self):
        if self.copy_radio.isChecked():
            self.chosen = (MODE_COPY, list(self.sources[self.copy_combo.currentIndex()]['classes']))
        elif self.file_radio.isChecked():
            classes = read_classes_file(self.file_edit.text())
            if not classes:
                self.error_label.setText('Choose a classes file with at least one class.')
                return
            self.chosen = (MODE_FILE, classes)
        else:
            self.chosen = (MODE_EMPTY, [])
        self.accept()

    def get_choice(self):
        """(mode, classes) or None when cancelled."""
        return self.chosen if self.exec_() else None
