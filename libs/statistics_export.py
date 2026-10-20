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


def _cell(ref, value=None, style=0, number=False):
    """One cell. None means a genuinely blank cell (no v / inline string)."""
    style_attr = ' s="%d"' % style if style else ''
    if value is None:
        return '<c r="%s"%s/>' % (ref, style_attr)
    if number:
        return '<c r="%s"%s><v>%d</v></c>' % (ref, style_attr, int(value))
    text = escape(str(value))
    return '<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style_attr, text)


def _row(row_no, cells):
    return '<row r="%d">%s</row>' % (row_no, ''.join(cells))


def _worksheet_xml(title, subtitle, results, columns):
    """Match the supplied roulette workbook: three rows per dataset and one blank separator."""
    report_columns = list(columns) + ['invalid_round_rate']
    last_col = _column_name(2 + len(report_columns))
    rows = []
    # Reference: styles extend across every title/subtitle column, even though only A has text.
    rows.append(_row(1, [_cell('%s1' % _column_name(i), title if i == 1 else None, 1)
                         for i in range(1, 3 + len(report_columns))]))
    rows.append(_row(2, [_cell('%s2' % _column_name(i), subtitle if i == 1 else None, 2)
                         for i in range(1, 3 + len(report_columns))]))
    headers = ['Dataset', 'total'] + report_columns
    rows.append(_row(4, [_cell('%s4' % _column_name(i + 1), text, 3 if i < 2 else 4)
                         for i, text in enumerate(headers)]))
    row_no = 5
    for result in results:
        values = [result['total']] + [result['counts'].get(name, 0) for name in columns]
        # The final invalid_round_rate has no ground-truth comparison in labelImg -> blank.
        values.append(None)
        cells = [_cell('A%d' % row_no, result['name'], 5)]
        cells.extend(_cell('%s%d' % (_column_name(i + 2), row_no), value,
                           6 if value in (0, None) else 7, number=value is not None)
                     for i, value in enumerate(values))
        rows.append(_row(row_no, cells))

        # Start incorrect at zero like the reference report; downstream analysis may replace it.
        cells = [_cell('A%d' % (row_no + 1), 'incorrect', 8)]
        cells.extend(_cell('%s%d' % (_column_name(i), row_no + 1), 0, 6, number=True)
                     for i in range(2, 3 + len(report_columns)))
        rows.append(_row(row_no + 1, cells))
        # Accuracy cannot be derived from labels alone, so it stays genuinely blank.
        cells = [_cell('A%d' % (row_no + 2), 'accuracy', 8)]
        cells.extend(_cell('%s%d' % (_column_name(i), row_no + 2), None, 6)
                     for i in range(2, 3 + len(report_columns)))
        rows.append(_row(row_no + 2, cells))
        row_no += 4

    widths = ['<col min="1" max="1" width="30" customWidth="1"/>',
              '<col min="2" max="2" width="12" customWidth="1"/>']
    if columns:
        widths.append('<col min="3" max="%d" width="16" customWidth="1"/>' % (2 + len(columns)))
    widths.append('<col min="%d" max="%d" width="20" customWidth="1"/>' %
                  (2 + len(report_columns), 2 + len(report_columns)))
    dimension = 'A1:%s%d' % (last_col, max(row_no - 2, 4))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<dimension ref="%s"/><sheetViews><sheetView workbookViewId="0"/></sheetViews>'
            '<sheetFormatPr defaultRowHeight="15"/><cols>%s</cols><sheetData>%s</sheetData>'
            '</worksheet>') % (dimension, ''.join(widths), ''.join(rows))


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
        'xl/styles.xml': '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="8"><font><color theme="1"/><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFE6EDF7"/><sz val="18"/></font><font><color rgb="FF9BA9C3"/><sz val="11"/></font><font><b/><color rgb="FF25344D"/><sz val="11"/></font><font><b/><color rgb="FF1B2940"/><sz val="11"/></font><font><color rgb="FF0D1626"/><sz val="11"/></font><font><b/><color rgb="FF20C25A"/><sz val="11"/></font><font><color rgb="FF5A6A8A"/><sz val="11"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF0F6FF"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9E5F5"/></patternFill></fill></fills><borders count="3"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border><border><left style="thin"><color rgb="FFCCCCCC"/></left><right style="thin"><color rgb="FFCCCCCC"/></right><top style="thin"><color rgb="FFCCCCCC"/></top><bottom style="thin"><color rgb="FFCCCCCC"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment horizontal="left" vertical="center"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="left" vertical="center"/></xf><xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="left" vertical="center" indent="1"/></xf><xf numFmtId="0" fontId="5" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="6" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="7" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="right" vertical="center" indent="3"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''',
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
