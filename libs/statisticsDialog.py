#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dataset selector, preview and Excel export for YOLO class statistics."""
import datetime
import os

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout,
                                 QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                                 QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
except ImportError:
    from PyQt4.QtCore import Qt
    from PyQt4.QtGui import (QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from libs.statistics_export import default_filename, scan_dataset, write_xlsx


class StatisticsDialog(QDialog):
    def __init__(self, title, datasets, start_dir=None, parent=None):
        super(StatisticsDialog, self).__init__(parent)
        self.title = title
        self.datasets = list(datasets)
        self.start_dir = start_dir or os.path.expanduser('~/Downloads')
        self.results = {}
        self.setWindowTitle('Export class statistics')
        self.resize(850, 520)

        layout = QVBoxLayout(self)
        intro = QLabel('Select datasets to export. Each dataset stays on its own row; no cross-dataset total is added.')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel('Datasets:'))
        self.dataset_list = QListWidget()
        for index, dataset in enumerate(self.datasets):
            item = QListWidgetItem(dataset['name'])
            item.setData(Qt.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.dataset_list.addItem(item)
        self.dataset_list.itemChanged.connect(lambda _item: self.update_preview())
        left_layout.addWidget(self.dataset_list, 1)
        select_row = QHBoxLayout()
        all_button = QPushButton('All')
        none_button = QPushButton('None')
        all_button.clicked.connect(lambda: self.select_all(True))
        none_button.clicked.connect(lambda: self.select_all(False))
        select_row.addWidget(all_button)
        select_row.addWidget(none_button)
        left_layout.addLayout(select_row)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel('Preview:'))
        self.preview = QTableWidget(0, 5)
        self.preview.setHorizontalHeaderLabels(['Dataset', 'Total boxes', 'Classes', 'Label files', 'Warnings'])
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self.preview, 1)
        self.summary = QLabel('')
        right_layout.addWidget(self.summary)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh')
        self.export_button = QPushButton('Export Excel…')
        close_button = QPushButton('Close')
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export)
        close_button.clicked.connect(self.reject)
        button_row.addWidget(self.refresh_button)
        button_row.addStretch(1)
        button_row.addWidget(self.export_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.refresh()

    def selected_indices(self):
        return [int(self.dataset_list.item(i).data(Qt.UserRole))
                for i in range(self.dataset_list.count())
                if self.dataset_list.item(i).checkState() == Qt.Checked]

    def selected_results(self):
        return [self.results[i] for i in self.selected_indices() if i in self.results]

    def select_all(self, checked):
        self.dataset_list.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.dataset_list.count()):
            self.dataset_list.item(i).setCheckState(state)
        self.dataset_list.blockSignals(False)
        self.update_preview()

    def refresh(self):
        self.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.results = {}
            for index, dataset in enumerate(self.datasets):
                self.results[index] = scan_dataset(dataset)
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)
        self.update_preview()

    def update_preview(self):
        results = self.selected_results()
        self.preview.setRowCount(len(results))
        warning_count = 0
        for row, result in enumerate(results):
            unknown = sum(1 for name in result['counts'] if name.startswith('ID '))
            warnings = []
            if result['malformed']:
                warnings.append('%d malformed row%s skipped' % (
                    result['malformed'], '' if result['malformed'] == 1 else 's'))
            if unknown:
                warnings.append('%d unknown class ID%s' % (unknown, '' if unknown == 1 else 's'))
            warning_count += bool(warnings)
            values = [result['name'], result['total'], len(result['counts']),
                      result['label_files'], '; '.join(warnings)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col in (1, 2, 3):
                    item.setTextAlignment(Qt.AlignCenter)
                self.preview.setItem(row, col, item)
        self.preview.resizeColumnsToContents()
        # Don't let an unusually long dataset name / warning make the dialog wider than the screen.
        for column in range(self.preview.columnCount()):
            self.preview.setColumnWidth(column, min(self.preview.columnWidth(column), 260))
        self.preview.horizontalHeader().setStretchLastSection(True)
        total_boxes = sum(r['total'] for r in results)
        self.summary.setText('%d dataset%s selected · %d boxes%s' % (
            len(results), '' if len(results) == 1 else 's', total_boxes,
            ' · %d dataset%s with warnings' % (warning_count, '' if warning_count == 1 else 's')
            if warning_count else ''))
        self.export_button.setEnabled(bool(results))

    def export(self):
        results = self.selected_results()
        if not results:
            return
        suggested = os.path.join(self.start_dir, default_filename(self.title))
        chosen = QFileDialog.getSaveFileName(self, 'Export class statistics', suggested,
                                             'Excel Workbook (*.xlsx)')
        path = chosen[0] if isinstance(chosen, tuple) else chosen
        if not path:
            return
        if not path.lower().endswith('.xlsx'):
            path += '.xlsx'
        try:
            write_xlsx(path, self.title, results, exported=datetime.date.today())
        except Exception as error:
            QMessageBox.critical(self, 'Export failed', 'Could not write:\n%s\n\n%s' % (path, error))
            return
        QMessageBox.information(self, 'Export complete', 'Saved %d dataset%s to:\n%s' % (
            len(results), '' if len(results) == 1 else 's', path))
