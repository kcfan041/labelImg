#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan YOLO labels and export per-dataset class counts to a styled .xlsx file.

Uses only the Python standard library, so the desktop launchers do not need another package.
"""
import datetime
import os
import re
import zipfile
from collections import OrderedDict
from xml.sax.saxutils import escape, quoteattr


def scan_dataset(dataset):
    """Return counts for one dataset dict ({name, labels, classes}).

    Every valid YOLO row counts as one box. Empty / malformed rows and classes.txt are skipped.
    Class ids outside classes.txt are retained under a visible ``ID N`` column.
    """
    classes = list(dataset.get('classes') or [])
    # Duplicate names are ambiguous in an Excel header; keep both rather than silently merging ids.
    display_names = []
    used_names = set()
    for class_id, name in enumerate(classes):
        display = name
        if display in used_names or classes.count(name) > 1:
            display = '%s (ID %d)' % (name, class_id)
        used_names.add(display)
        display_names.append(display)
    counts = OrderedDict((name, 0) for name in display_names)
    unknown = {}
    label_files = 0
    malformed = 0
    labels_dir = dataset.get('labels')
    if labels_dir and os.path.isdir(labels_dir):
        for root, dirs, files in os.walk(labels_dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for filename in sorted(files):
                if not filename.lower().endswith('.txt') or filename.lower() == 'classes.txt':
                    continue
                label_files += 1
                path = os.path.join(root, filename)
                try:
                    with open(path, 'r', encoding='utf-8', errors='replace') as f:
                        for line in f:
                            fields = line.split()
                            if not fields:
                                continue
                            if len(fields) < 5:
                                malformed += 1
                                continue
                            try:
                                class_id = int(fields[0])
                                # The four YOLO coordinates must be numeric for this to be a box.
                                tuple(float(v) for v in fields[1:5])
                            except ValueError:
                                malformed += 1
                                continue
                            if 0 <= class_id < len(classes):
                                counts[display_names[class_id]] += 1
                            else:
                                unknown[class_id] = unknown.get(class_id, 0) + 1
                except OSError:
                    malformed += 1
    for class_id in sorted(unknown):
        counts['ID %d' % class_id] = unknown[class_id]
    return {
        'name': dataset.get('name') or os.path.basename(dataset.get('root', '')),
        'counts': counts,
        'total': sum(counts.values()),
        'label_files': label_files,
        'malformed': malformed,
    }


def class_columns(results):
    """Union of class names, preserving each selected dataset's classes.txt order."""
    seen = set()
    columns = []
    for result in results:
        for name in result['counts']:
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|]+', '_', name).strip(' .')
    return name or 'statistics'


def default_filename(title, today=None):
    today = today or datetime.date.today()
    return '%s_%s.xlsx' % (safe_filename(title), today.isoformat())


def _column_name(number):
    result = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell(ref, value, style=0, number=False):
    style_attr = ' s="%d"' % style if style else ''
    if number:
        return '<c r="%s"%s><v>%d</v></c>' % (ref, style_attr, int(value))
    text = escape(str(value))
    return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style_attr, text)


def _worksheet_xml(title, subtitle, results, columns):
    last_col = _column_name(2 + len(columns))
    rows = []
    rows.append('<row r="1" ht="28" customHeight="1">%s</row>' % _cell('A1', title, 1))
    rows.append('<row r="2">%s</row>' % _cell('A2', subtitle, 2))
    headers = ['Dataset', 'total'] + columns
    rows.append('<row r="4" ht="22" customHeight="1">%s</row>' % ''.join(
        _cell('%s4' % _column_name(i + 1), text, 3) for i, text in enumerate(headers)))
    row_no = 5
    for result in results:
        values = [result['name'], result['total']] + [result['counts'].get(name, 0) for name in columns]
        cells = [_cell('A%d' % row_no, values[0], 4)]
        cells.extend(_cell('%s%d' % (_column_name(i + 1), row_no), value, 5, number=True)
                     for i, value in enumerate(values[1:], 1))
        rows.append('<row r="%d" ht="20" customHeight="1">%s</row>' % (row_no, ''.join(cells)))
        row_no += 2  # blank row between datasets, like the reference workbook
    widths = ['<col min="1" max="1" width="30" customWidth="1"/>',
              '<col min="2" max="2" width="12" customWidth="1"/>']
    if columns:
        widths.append('<col min="3" max="%d" width="12" customWidth="1"/>' % (2 + len(columns)))
    dimension = 'A1:%s%d' % (last_col, max(row_no - 2, 4))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<dimension ref="%s"/><sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/>'
            '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
            '<cols>%s</cols><sheetData>%s</sheetData><autoFilter ref="A4:%s4"/>'
            '</worksheet>') % (dimension, ''.join(widths), ''.join(rows), last_col)


def write_xlsx(path, title, results, exported=None):
    """Write the selected datasets as separate rows. There is deliberately no total row."""
    if not results:
        raise ValueError('No datasets selected')
    exported = exported or datetime.date.today()
    columns = class_columns(results)
    subtitle = 'Exported: %s · %d dataset%s' % (
        exported.isoformat(), len(results), '' if len(results) == 1 else 's')
    sheet_name = re.sub(r'[\\/:*?\[\]]+', '_', title + ' 品質分析').strip("'")[:31] or 'Statistics'
    worksheet = _worksheet_xml(title, subtitle, results, columns)
    files = {
        '[Content_Types].xml': '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''',
        '_rels/.rels': '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        'xl/workbook.xml': '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name=%s sheetId="1" r:id="rId1"/></sheets></workbook>' % quoteattr(sheet_name),
        'xl/_rels/workbook.xml.rels': '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        'xl/worksheets/sheet1.xml': worksheet,
        'xl/styles.xml': '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="3"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="Arial"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF5B9BD5"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FFD9E2F3"/></left><right style="thin"><color rgb="FFD9E2F3"/></right><top style="thin"><color rgb="FFD9E2F3"/></top><bottom style="thin"><color rgb="FFD9E2F3"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment horizontal="left"/></xf><xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment horizontal="center"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''',
    }
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    temp = path + '.tmp'
    try:
        with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as book:
            for name, content in files.items():
                book.writestr(name, content.encode('utf-8'))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return columns
