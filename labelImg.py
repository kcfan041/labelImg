#!/usr/bin/env python
# -*- coding: utf-8 -*-
import argparse
import codecs
import os.path
import platform
import shutil
import sys
import webbrowser as wb
from collections import OrderedDict
from functools import partial

try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip
        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

from libs.combobox import ComboBox
from libs.default_label_combobox import DefaultLabelComboBox
from libs.resources import *
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.lightWidget import LightWidget
from libs.labelDialog import LabelDialog
from libs.classesDialog import ClassesDialog, MODE_EMPTY, read_classes_file, write_classes_file, count_label_files
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError, LabelFileFormat
from libs.toolBar import ToolBar
from libs.pascal_voc_io import PascalVocReader
from libs.pascal_voc_io import XML_EXT
from libs.yolo_io import YoloReader
from libs.yolo_io import TXT_EXT
from libs.create_ml_io import CreateMLReader
from libs.create_ml_io import JSON_EXT
from libs.ustr import ustr
from libs.hashableQListWidgetItem import HashableQListWidgetItem
from libs.physical_shortcuts import PhysicalKeyShortcuts
from libs.stayOpenMenu import StayOpenMenu
from libs.shortcutsDialog import ShortcutsDialog, normalize as normalize_key
from libs.statisticsDialog import StatisticsDialog

__appname__ = 'labelImg'
SORT_BY_NAME = 'Filename'
SORT_BY_LABELS = 'Labels'
NEW_BOX_CONFIDENCE = '1.0'  # confidence text written for boxes drawn with Create box


class WindowMixin(object):

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            add_actions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            add_actions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


ALL_CLASSES_ITEM = 'All classes'


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self, default_filename=None, default_save_dir=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        self.os_name = platform.system()

        # Load string bundle for i18n
        self.string_bundle = StringBundle.get_bundle()
        get_str = lambda str_id: self.string_bundle.get_string(str_id)

        # Save as Pascal voc xml
        self.default_save_dir = default_save_dir
        self.label_file_format = settings.get(SETTING_LABEL_FILE_FORMAT, LabelFileFormat.PASCAL_VOC)

        # For loading all image under a directory
        self.m_img_list = []
        self.m_img_full_list = []  # unfiltered master list
        self.m_img_cache = {}      # img_path -> {classes:set, count:int, min_conf:float|None}
        self._rebuild_blocked = False
        # Multi-dataset mode (Open Dir on a folder of datasets, each with images/ labels/ classes.txt):
        # list of {'root', 'images', 'labels', 'classes_file', 'classes'}; empty when not in this mode
        self.sub_datasets = []
        self.multi_root = None
        self._active_dataset = None
        self.dir_name = None
        self.label_hist = []
        self.last_open_dir = None
        self.cur_img_idx = 0
        self.img_count = len(self.m_img_list)

        # Whether we need to save or not.
        self.dirty = False

        self._no_selection_slot = False
        self._beginner = True
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        # The class list comes from the opened dataset's classes.txt (asked for when it's missing);
        # there is no built-in default list
        self.default_label = ''
        self.classes_file_path = None
        # False when a dataset has labels but no classes.txt: ids are shown and saved as plain numbers
        self.classes_known = True
        self._current_classes = None  # dataset whose class list is in use, for the status bar

        # Main widgets and related state.
        self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)

        self.items_to_shapes = {}
        self.shapes_to_items = {}
        self.prev_label_text = ''

        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 0)

        # Use default label: new boxes get the chosen class without the label dialog
        self.use_default_label_checkbox = QCheckBox(get_str('useDefaultLabel'))
        self.use_default_label_checkbox.setChecked(False)
        self.use_default_label_checkbox.setToolTip('New boxes get this class directly, without asking')
        self.default_label_combo_box = DefaultLabelComboBox(self,items=self.label_hist)
        self.default_label_combo_box.layout().setContentsMargins(0, 0, 0, 0)
        self.default_label_combo_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Difficult flag: only Pascal VOC stores it (YOLO drops it), so it's hidden from the panel.
        # The widget is kept because loading / drawing shapes still reads and resets it.
        self.diffc_button = QCheckBox(get_str('useDifficult'))
        self.diffc_button.setChecked(False)
        self.diffc_button.stateChanged.connect(self.button_state)
        self.diffc_button.setVisible(False)

        # Always-visible class list: click a class to relabel the selected box, or with no box
        # selected, to pick the class new boxes get (replaces opening the Edit Label dialog)
        self.class_list = QListWidget()
        self.class_list.setToolTip('Click: set the selected box to this class.\n'
                                   'With no box selected: new boxes get this class.')
        self.class_list.itemClicked.connect(self.class_list_clicked)
        self.class_list.setFocusPolicy(Qt.NoFocus)  # keep keys (a / d / w ...) on the canvas
        self.use_default_label_checkbox.toggled.connect(lambda _: self.highlight_class_list())

        # Quick on/off for what's drawn on the image (boxes, and the class text above them)
        self.show_boxes_checkbox = QCheckBox(get_str('showBoxes'))
        self.show_boxes_checkbox.setChecked(True)
        self.show_boxes_checkbox.toggled.connect(self.toggle_show_boxes)
        self.show_label_text_checkbox = QCheckBox(get_str('displayLabel'))

        # Show only the boxes of one class on this image
        self.combo_box = ComboBox(self)
        self.combo_box.layout().setContentsMargins(0, 0, 0, 0)
        self.combo_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.combo_box.setToolTip('Show only the boxes of one class on this image')

        # Compact form: one row per setting, with a caption on the left
        panel = QWidget()
        form = QGridLayout(panel)
        form.setContentsMargins(4, 2, 4, 2)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(2)
        form.addWidget(QLabel('Show:'), 0, 0)
        form.addWidget(self.show_boxes_checkbox, 0, 1)
        form.addWidget(self.show_label_text_checkbox, 0, 2)
        form.addWidget(QLabel('New box:'), 1, 0)
        form.addWidget(self.use_default_label_checkbox, 1, 1)
        form.addWidget(self.default_label_combo_box, 1, 2)
        form.addWidget(QLabel('Filter:'), 2, 0)
        form.addWidget(self.combo_box, 2, 1, 1, 2)
        form.addWidget(self.diffc_button, 3, 0, 1, 3)
        form.setColumnStretch(2, 1)

        list_layout.setSpacing(2)
        list_layout.addWidget(panel)
        list_layout.addWidget(QLabel(' Classes:'))
        list_layout.addWidget(self.class_list)
        list_layout.addWidget(QLabel(' Boxes:'))

        # Create and add a widget for showing current label items
        self.label_list = QListWidget()
        label_list_container = QWidget()
        label_list_container.setLayout(list_layout)
        self.label_list.itemActivated.connect(self.label_selection_changed)
        self.label_list.itemSelectionChanged.connect(self.label_selection_changed)
        self.label_list.itemDoubleClicked.connect(self.edit_label)
        # Connect to itemChanged to detect checkbox changes.
        self.label_list.itemChanged.connect(self.label_item_changed)
        list_layout.addWidget(self.label_list)



        self.dock = QDockWidget(get_str('boxLabelText'), self)
        self.dock.setObjectName(get_str('labels'))
        self.dock.setWidget(label_list_container)

        self.file_list_widget = QListWidget()
        self.file_list_widget.itemDoubleClicked.connect(self.file_item_double_clicked)
        file_list_layout = QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(2)

        filter_bar = QWidget()
        grid = QGridLayout(filter_bar)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setSpacing(2)

        self.filter_name_edit = QLineEdit()
        self.filter_name_edit.setPlaceholderText('filename contains…')
        self.filter_name_edit.setClearButtonEnabled(True)
        self.filter_name_edit.textChanged.connect(lambda _: self.apply_filter_change())
        grid.addWidget(QLabel('Name:'), 0, 0)
        grid.addWidget(self.filter_name_edit, 0, 1, 1, 3)

        # Stays open while ticking classes; closes on a click outside / Esc
        self.filter_class_menu = StayOpenMenu(self)
        self.filter_class_button = QToolButton()
        self.filter_class_button.setText('All classes')
        self.filter_class_button.setPopupMode(QToolButton.InstantPopup)
        self.filter_class_button.setMenu(self.filter_class_menu)
        self.filter_class_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.filter_class_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(QLabel('Class:'), 1, 0)
        grid.addWidget(self.filter_class_button, 1, 1, 1, 3)

        self.filter_count_min = QSpinBox()
        self.filter_count_min.setRange(0, 9999)
        self.filter_count_min.setValue(0)
        self.filter_count_max = QSpinBox()
        self.filter_count_max.setRange(0, 9999)
        self.filter_count_max.setValue(9999)
        self.filter_count_min.valueChanged.connect(lambda _: self.apply_filter_change())
        self.filter_count_max.valueChanged.connect(lambda _: self.apply_filter_change())
        grid.addWidget(QLabel('Count:'), 2, 0)
        grid.addWidget(self.filter_count_min, 2, 1)
        grid.addWidget(QLabel('—'), 2, 2)
        grid.addWidget(self.filter_count_max, 2, 3)

        self.filter_conf_check = QCheckBox('min conf <')
        self.filter_conf_value = QDoubleSpinBox()
        self.filter_conf_value.setRange(0.0, 1.0)
        self.filter_conf_value.setSingleStep(0.05)
        self.filter_conf_value.setDecimals(2)
        self.filter_conf_value.setValue(0.90)
        self.filter_conf_check.stateChanged.connect(lambda _: self.apply_filter_change())
        self.filter_conf_value.valueChanged.connect(lambda _: self.apply_filter_change())
        grid.addWidget(self.filter_conf_check, 3, 0, 1, 2)
        grid.addWidget(self.filter_conf_value, 3, 2, 1, 2)

        # Order of the file list (see sort_image_list). Kept by Clear, remembered in the settings.
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([SORT_BY_NAME, SORT_BY_LABELS])
        self.sort_combo.setToolTip('Labels: by the class ids in each label file (0,1 before 0,5),\n'
                                   'then by filename. Images without boxes come first.\n'
                                   'With several datasets, each one is sorted on its own.')
        self.sort_combo.setCurrentText(settings.get(SETTING_SORT_MODE, SORT_BY_NAME))
        self.sort_combo.currentIndexChanged.connect(lambda _: self.apply_sort_change())
        self.resort_button = QToolButton()
        self.resort_button.setText('Re-sort')
        self.resort_button.setToolTip('Saving doesn\'t move images in the list; sort again with the latest labels')
        self.resort_button.clicked.connect(lambda _=False: self.resort())
        self.resort_button.setEnabled(self.sort_combo.currentText() == SORT_BY_LABELS)
        grid.addWidget(QLabel('Sort:'), 4, 0)
        grid.addWidget(self.sort_combo, 4, 1, 1, 2)
        grid.addWidget(self.resort_button, 4, 3)

        self.filter_stats_label = QLabel('')
        clear_btn = QPushButton('Clear')
        clear_btn.clicked.connect(self.clear_filters)
        grid.addWidget(self.filter_stats_label, 5, 0, 1, 3)
        grid.addWidget(clear_btn, 5, 3)

        self.statistics_button = QPushButton('Export Statistics…')
        self.statistics_button.setToolTip('Count every class in one or more datasets and export an Excel workbook')
        self.statistics_button.clicked.connect(self.show_statistics)
        self.statistics_button.setEnabled(False)
        grid.addWidget(self.statistics_button, 6, 0, 1, 4)

        file_list_layout.addWidget(filter_bar)
        file_list_layout.addWidget(self.file_list_widget)
        file_list_container = QWidget()
        file_list_container.setLayout(file_list_layout)
        self.file_dock = QDockWidget(get_str('fileList'), self)
        self.file_dock.setObjectName(get_str('files'))
        self.file_dock.setWidget(file_list_container)

        self.zoom_widget = ZoomWidget()
        self.light_widget = LightWidget(get_str('lightWidgetTitle'))
        self.color_dialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoom_request)
        self.canvas.zoomAtRequest.connect(self.zoom_at)
        self.canvas.panRequest.connect(self.pan_request)
        self.undo_history = OrderedDict()  # image path -> undo/redo snapshots, see record_edit
        self._restoring_undo = False
        self._zoom_exact = None  # unrounded zoom %, see zoom_at
        self.canvas.lightRequest.connect(self.light_request)
        self.canvas.set_drawing_shape_to_square(settings.get(SETTING_DRAW_SQUARE, False))

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scroll_bars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scroll_area = scroll
        scroll.installEventFilter(self)
        self.canvas.scrollRequest.connect(self.scroll_request)

        self.canvas.newShape.connect(self.new_shape)
        self.canvas.shapeMoved.connect(self.set_dirty)
        self.canvas.editStarted.connect(self.on_canvas_edit)
        self.canvas.selectionChanged.connect(self.shape_selection_changed)
        self.canvas.drawingPolygon.connect(self.toggle_drawing_sensitive)

        self.setCentralWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.file_dock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.dock_features = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        self.dock.setFeatures(self.dock.features() ^ self.dock_features)

        # Actions
        action = partial(new_action, self)
        quit = action(get_str('quit'), self.close,
                      'Ctrl+Q', 'quit', get_str('quitApp'))

        open = action(get_str('openFile'), self.open_file,
                      'Ctrl+O', 'open', get_str('openFileDetail'))

        open_dir = action(get_str('openDir'), self.open_dir_dialog,
                          'Ctrl+u', 'open', get_str('openDir'))

        change_save_dir = action(get_str('changeSaveDir'), self.change_save_dir_dialog,
                                 'Ctrl+r', 'open', get_str('changeSavedAnnotationDir'))

        open_annotation = action(get_str('openAnnotation'), self.open_annotation_dialog,
                                 'Ctrl+Shift+O', 'open', get_str('openAnnotationDetail'))
        copy_prev_bounding = action(get_str('copyPrevBounding'), self.copy_previous_bounding_boxes, 'Ctrl+v', 'copy', get_str('copyPrevBounding'))

        open_next_image = action(get_str('nextImg'), self.open_next_image,
                                 'd', 'next', get_str('nextImgDetail'))

        open_prev_image = action(get_str('prevImg'), self.open_prev_image,
                                 'a', 'prev', get_str('prevImgDetail'))
        # Holding these keys steps through images (see libs/physical_shortcuts.py)
        self.hold_repeat_actions = [open_next_image, open_prev_image]

        verify = action(get_str('verifyImg'), self.verify_image,
                        'space', 'verify', get_str('verifyImgDetail'))

        save = action(get_str('save'), self.save_file,
                      'Ctrl+S', 'save', get_str('saveDetail'), enabled=False)

        def get_format_meta(format):
            """
            returns a tuple containing (title, icon_name) of the selected format
            """
            if format == LabelFileFormat.PASCAL_VOC:
                return '&PascalVOC', 'format_voc'
            elif format == LabelFileFormat.YOLO:
                return '&YOLO', 'format_yolo'
            elif format == LabelFileFormat.CREATE_ML:
                return '&CreateML', 'format_createml'

        save_format = action(get_format_meta(self.label_file_format)[0],
                             self.change_format, 'Ctrl+Y',
                             get_format_meta(self.label_file_format)[1],
                             get_str('changeSaveFormat'), enabled=True)

        save_as = action(get_str('saveAs'), self.save_file_as,
                         'Ctrl+Shift+S', 'save-as', get_str('saveAsDetail'), enabled=False)

        close = action(get_str('closeCur'), self.close_file, 'Ctrl+W', 'close', get_str('closeCurDetail'))

        delete_image = action(get_str('deleteImg'), self.delete_image, 'Ctrl+Shift+D', 'close', get_str('deleteImgDetail'))

        reset_all = action(get_str('resetAll'), self.reset_all, None, 'resetall', get_str('resetAllDetail'))

        color1 = action(get_str('boxLineColor'), self.choose_color1,
                        'Ctrl+L', 'color_line', get_str('boxLineColorDetail'))

        create_mode = action(get_str('crtBox'), self.set_create_mode,
                             'w', 'new', get_str('crtBoxDetail'), enabled=False)
        edit_mode = action(get_str('editBox'), self.set_edit_mode,
                           'Ctrl+J', 'edit', get_str('editBoxDetail'), enabled=False)

        create = action(get_str('crtBox'), self.create_shape,
                        'w', 'new', get_str('crtBoxDetail'), enabled=False)
        delete = action(get_str('delBox'), self.delete_selected_shape,
                        ['x', 'Delete', 'Backspace'], 'delete', get_str('delBoxDetail'), enabled=False)
        copy = action(get_str('dupBox'), self.copy_selected_shape,
                      'Ctrl+D', 'copy', get_str('dupBoxDetail'),
                      enabled=False)
        # 'Ctrl' is Cmd on macOS. Not QKeySequence.Redo: on Windows that includes Ctrl+Y,
        # which is already the save-format toggle.
        undo = action('Undo', self.undo_edit, 'Ctrl+Z', 'undo', 'Undo the last box edit on this image',
                      enabled=False)
        redo = action('Redo', self.redo_edit, 'Ctrl+Shift+Z', 'undo', 'Redo the box edit undone last',
                      enabled=False)

        advanced_mode = action(get_str('advancedMode'), self.toggle_advanced_mode,
                               'Ctrl+Shift+A', 'expert', get_str('advancedModeDetail'),
                               checkable=True)

        hide_all = action(get_str('hideAllBox'), partial(self.toggle_polygons, False),
                          'Ctrl+H', 'hide', get_str('hideAllBoxDetail'),
                          enabled=False)
        show_all = action(get_str('showAllBox'), partial(self.toggle_polygons, True),
                          'Ctrl+A', 'hide', get_str('showAllBoxDetail'),
                          enabled=False)

        help_default = action(get_str('tutorialDefault'), self.show_default_tutorial_dialog, None, 'help', get_str('tutorialDetail'))
        show_info = action(get_str('info'), self.show_info_dialog, None, 'help', get_str('info'))
        show_shortcut = action(get_str('shortcut'), self.show_shortcuts_dialog, None, 'help', get_str('shortcut'))

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoom_widget)
        self.zoom_widget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (format_shortcut("Ctrl+[-+]"),
                                             format_shortcut("Ctrl+Wheel")))
        self.zoom_widget.setEnabled(False)

        zoom_in = action(get_str('zoomin'), partial(self.add_zoom, 10),
                         'Ctrl++', 'zoom-in', get_str('zoominDetail'), enabled=False)
        zoom_out = action(get_str('zoomout'), partial(self.add_zoom, -10),
                          'Ctrl+-', 'zoom-out', get_str('zoomoutDetail'), enabled=False)
        zoom_org = action(get_str('originalsize'), partial(self.set_zoom, 100),
                          'Ctrl+=', 'zoom', get_str('originalsizeDetail'), enabled=False)
        fit_window = action(get_str('fitWin'), self.set_fit_window,
                            'Ctrl+F', 'fit-window', get_str('fitWinDetail'),
                            checkable=True, enabled=False)
        fit_width = action(get_str('fitWidth'), self.set_fit_width,
                           'Ctrl+Shift+F', 'fit-width', get_str('fitWidthDetail'),
                           checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoom_actions = (self.zoom_widget, zoom_in, zoom_out,
                        zoom_org, fit_window, fit_width)
        self.zoom_mode = self.MANUAL_ZOOM
        self.scalers = {
            self.FIT_WINDOW: self.scale_fit_window,
            self.FIT_WIDTH: self.scale_fit_width,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        light = QWidgetAction(self)
        light.setDefaultWidget(self.light_widget)
        self.light_widget.setWhatsThis(
            u"Brighten or darken current image. Also accessible with"
            " %s and %s from the canvas." % (format_shortcut("Ctrl+Shift+[-+]"),
                                             format_shortcut("Ctrl+Shift+Wheel")))
        self.light_widget.setEnabled(False)

        light_brighten = action(get_str('lightbrighten'), partial(self.add_light, 10),
                                'Ctrl+Shift++', 'light_lighten', get_str('lightbrightenDetail'), enabled=False)
        light_darken = action(get_str('lightdarken'), partial(self.add_light, -10),
                              'Ctrl+Shift+-', 'light_darken', get_str('lightdarkenDetail'), enabled=False)
        light_org = action(get_str('lightreset'), partial(self.set_light, 50),
                           'Ctrl+Shift+=', 'light_reset', get_str('lightresetDetail'), checkable=True, enabled=False)
        light_org.setChecked(True)

        # Group light controls into a list for easier toggling.
        light_actions = (self.light_widget, light_brighten,
                         light_darken, light_org)

        edit = action(get_str('editLabel'), self.edit_label,
                      'Ctrl+E', 'edit', get_str('editLabelDetail'),
                      enabled=False)

        shape_line_color = action(get_str('shapeLineColor'), self.choose_shape_line_color,
                                  icon='color_line', tip=get_str('shapeLineColorDetail'),
                                  enabled=False)
        shape_fill_color = action(get_str('shapeFillColor'), self.choose_shape_fill_color,
                                  icon='color', tip=get_str('shapeFillColorDetail'),
                                  enabled=False)

        labels = self.dock.toggleViewAction()
        labels.setText(get_str('showHide'))
        labels.setShortcut('Ctrl+Shift+L')

        # Label list context menu.
        label_menu = QMenu()
        add_actions(label_menu, (edit, delete))
        self.label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label_list.customContextMenuRequested.connect(
            self.pop_label_list_menu)

        # Draw squares/rectangles
        self.draw_squares_option = QAction(get_str('drawSquares'), self)
        self.draw_squares_option.setShortcut('Ctrl+Shift+R')
        self.draw_squares_option.setCheckable(True)
        self.draw_squares_option.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.draw_squares_option.triggered.connect(self.toggle_draw_square)

        # Store actions for further handling.
        self.actions = Struct(save=save, save_format=save_format, saveAs=save_as, open=open, close=close, resetAll=reset_all, deleteImg=delete_image,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy, undo=undo, redo=redo,
                              createMode=create_mode, editMode=edit_mode, advancedMode=advanced_mode,
                              shapeLineColor=shape_line_color, shapeFillColor=shape_fill_color,
                              zoom=zoom, zoomIn=zoom_in, zoomOut=zoom_out, zoomOrg=zoom_org,
                              fitWindow=fit_window, fitWidth=fit_width,
                              zoomActions=zoom_actions,
                              lightBrighten=light_brighten, lightDarken=light_darken, lightOrg=light_org,
                              lightActions=light_actions,
                              fileMenuActions=(
                                  open, open_dir, save, save_as, close, reset_all, quit),
                              beginner=(), advanced=(),
                              editMenu=(undo, redo, None, edit, copy, delete,
                                        None, color1, self.draw_squares_option),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(create_mode, edit_mode, edit, copy,
                                               delete, shape_line_color, shape_fill_color),
                              onLoadActive=(
                                  close, create, create_mode, edit_mode),
                              onShapesPresent=(save_as, hide_all, show_all))

        self.menus = Struct(
            file=self.menu(get_str('menu_file')),
            edit=self.menu(get_str('menu_edit')),
            view=self.menu(get_str('menu_view')),
            help=self.menu(get_str('menu_help')),
            recentFiles=QMenu(get_str('menu_openRecent')),
            labelList=label_menu)

        # Auto saving : Enable auto saving if pressing next
        self.auto_saving = QAction(get_str('autoSaveMode'), self)
        self.auto_saving.setCheckable(True)
        self.auto_saving.setChecked(settings.get(SETTING_AUTO_SAVE, True))
        # Sync single class mode from PR#106
        self.single_class_mode = QAction(get_str('singleClsMode'), self)
        self.single_class_mode.setShortcut("Ctrl+Shift+C")  # Ctrl+Shift+S is Save As
        self.single_class_mode.setCheckable(True)
        self.single_class_mode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.display_label_option = QAction(get_str('displayLabel'), self)
        self.display_label_option.setShortcut("Ctrl+Shift+P")
        self.display_label_option.setCheckable(True)
        self.display_label_option.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.display_label_option.triggered.connect(self.toggle_paint_labels_option)
        # Right-panel checkbox mirrors the View menu item
        self.show_label_text_checkbox.setChecked(self.display_label_option.isChecked())
        self.show_label_text_checkbox.toggled.connect(self.set_paint_labels)

        # Keyboard shortcut editor. Ids are stable so saved settings survive text changes;
        # actions sharing an id (the beginner / advanced 'w') always get the same keys.
        edit_shortcuts = action('Keyboard Shortcuts…', self.edit_shortcuts, 'Ctrl+,', 'help',
                                'Change the keyboard shortcuts')
        edit_shortcuts.setMenuRole(QAction.NoRole)  # stay in the menu on macOS
        self.shortcut_entries = [
            ('next_image', 'Next image', [open_next_image]),
            ('prev_image', 'Previous image', [open_prev_image]),
            ('create_box', 'Create box', [create, create_mode]),
            ('edit_mode', 'Edit mode (advanced)', [edit_mode]),
            ('delete_box', 'Delete box', [delete]),
            ('copy_box', 'Duplicate box', [copy]),
            ('edit_label', 'Edit label (dialog)', [edit]),
            ('undo', 'Undo', [undo]),
            ('redo', 'Redo', [redo]),
            ('verify', 'Verify image', [verify]),
            ('save', 'Save', [save]),
            ('save_as', 'Save as', [save_as]),
            ('save_format', 'Change save format', [save_format]),
            ('open', 'Open file', [open]),
            ('open_dir', 'Open dir', [open_dir]),
            ('change_save_dir', 'Change save dir', [change_save_dir]),
            ('open_annotation', 'Open annotation', [open_annotation]),
            ('copy_prev', 'Copy previous bounding boxes', [copy_prev_bounding]),
            ('close', 'Close image', [close]),
            ('delete_image', 'Delete image', [delete_image]),
            ('quit', 'Quit', [quit]),
            ('box_line_color', 'Box line color', [color1]),
            ('draw_squares', 'Draw squares', [self.draw_squares_option]),
            ('advanced_mode', 'Advanced mode', [advanced_mode]),
            ('auto_save', 'Auto save mode', [self.auto_saving]),
            ('single_class', 'Single class mode', [self.single_class_mode]),
            ('display_labels', 'Display labels', [self.display_label_option]),
            ('toggle_panel', 'Show / hide Box Labels panel', [labels]),
            ('hide_all', 'Hide all boxes', [hide_all]),
            ('show_all', 'Show all boxes', [show_all]),
            ('zoom_in', 'Zoom in', [zoom_in]),
            ('zoom_out', 'Zoom out', [zoom_out]),
            ('zoom_org', 'Original size', [zoom_org]),
            ('fit_window', 'Fit window', [fit_window]),
            ('fit_width', 'Fit width', [fit_width]),
            ('light_brighten', 'Brighten', [light_brighten]),
            ('light_darken', 'Darken', [light_darken]),
            ('light_org', 'Original brightness', [light_org]),
            ('shortcuts', 'Keyboard shortcuts', [edit_shortcuts]),
        ]
        self.default_shortcuts = dict(
            (eid, [normalize_key(k) for k in acts[0].shortcuts()]) for eid, _name, acts in self.shortcut_entries)
        self.apply_shortcuts(settings.get(SETTING_SHORTCUTS, {}))

        add_actions(self.menus.file,
                    (open, open_dir, change_save_dir, open_annotation, copy_prev_bounding, self.menus.recentFiles, save, save_format, save_as, close, reset_all, delete_image, None, edit_shortcuts, quit))
        add_actions(self.menus.help, (help_default, show_info, show_shortcut))
        add_actions(self.menus.view, (
            self.auto_saving,
            self.single_class_mode,
            self.display_label_option,
            labels, advanced_mode, None,
            hide_all, show_all, None,
            zoom_in, zoom_out, zoom_org, None,
            fit_window, fit_width, None,
            light_brighten, light_darken, light_org))

        self.menus.file.aboutToShow.connect(self.update_file_menu)

        # Custom context menu for the canvas widget:
        add_actions(self.canvas.menus[0], self.actions.beginnerContext)
        add_actions(self.canvas.menus[1], (
            action('&Copy here', self.copy_shape),
            action('&Move here', self.move_shape)))

        self.tools = self.toolbar('Tools')
        self.actions.beginner = (
            open, open_dir, change_save_dir, open_next_image, open_prev_image, verify, save, save_format, None, create, copy, delete, None,
            zoom_in, zoom, zoom_out, fit_window, fit_width, None,
            light_brighten, light, light_darken, light_org)

        self.actions.advanced = (
            open, open_dir, change_save_dir, open_next_image, open_prev_image, save, save_format, None,
            create_mode, edit_mode, None,
            hide_all, show_all)

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.file_path = ustr(default_filename)
        self.last_open_dir = None
        self.recent_files = []
        self.max_recent = 7
        self.line_color = None
        self.fill_color = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        # Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recent_file_qstring_list = settings.get(SETTING_RECENT_FILES)
                self.recent_files = [ustr(i) for i in recent_file_qstring_list]
            else:
                self.recent_files = recent_file_qstring_list = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        save_dir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.last_open_dir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))
        if self.default_save_dir is None and save_dir is not None and os.path.exists(save_dir):
            self.default_save_dir = save_dir
            self.statusBar().showMessage('%s started. Annotation will be saved to %s' %
                                         (__appname__, self.default_save_dir))
            self.statusBar().show()

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.line_color = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fill_color = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.set_drawing_color(self.line_color)
        # Add chris
        Shape.difficult = self.difficult

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggle_advanced_mode()

        # Populate the File menu dynamically.
        self.update_file_menu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.file_path and os.path.isdir(self.file_path):
            self.queue_event(partial(self.import_dir_images, self.file_path or ""))
        elif self.file_path:
            self.queue_event(partial(self.load_file, self.file_path or ""))

        # Callbacks:
        self.zoom_widget.valueChanged.connect(self.paint_canvas)
        self.light_widget.valueChanged.connect(self.paint_canvas)

        self.populate_mode_actions()

        # Display cursor coordinates at the right of status bar
        self.label_coordinates = QLabel('')
        self.statusBar().addPermanentWidget(self.label_coordinates)
        # Which classes.txt is in use, always visible
        self.classes_status_label = QLabel('')
        self.statusBar().addPermanentWidget(self.classes_status_label)
        self.update_classes_status()

        # Open Dir if default file
        if self.file_path and os.path.isdir(self.file_path):
            self.open_dir_dialog(dir_path=self.file_path, silent=True)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.canvas.set_drawing_shape_to_square(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.set_drawing_shape_to_square(True)

    # Support Functions #
    def set_format(self, save_format):
        if save_format == FORMAT_PASCALVOC:
            self.actions.save_format.setText(FORMAT_PASCALVOC)
            self.actions.save_format.setIcon(new_icon("format_voc"))
            self.label_file_format = LabelFileFormat.PASCAL_VOC
            LabelFile.suffix = XML_EXT

        elif save_format == FORMAT_YOLO:
            self.actions.save_format.setText(FORMAT_YOLO)
            self.actions.save_format.setIcon(new_icon("format_yolo"))
            self.label_file_format = LabelFileFormat.YOLO
            LabelFile.suffix = TXT_EXT

        elif save_format == FORMAT_CREATEML:
            self.actions.save_format.setText(FORMAT_CREATEML)
            self.actions.save_format.setIcon(new_icon("format_createml"))
            self.label_file_format = LabelFileFormat.CREATE_ML
            LabelFile.suffix = JSON_EXT

    def change_format(self):
        if self.label_file_format == LabelFileFormat.PASCAL_VOC:
            self.set_format(FORMAT_YOLO)
        elif self.label_file_format == LabelFileFormat.YOLO:
            self.set_format(FORMAT_CREATEML)
        elif self.label_file_format == LabelFileFormat.CREATE_ML:
            self.set_format(FORMAT_PASCALVOC)
        else:
            raise ValueError('Unknown label file format.')
        self.set_dirty()

    def no_shapes(self):
        return not self.items_to_shapes

    def toggle_advanced_mode(self, value=True):
        self._beginner = not value
        self.canvas.set_editing(True)
        self.populate_mode_actions()
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dock_features)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dock_features)

    def populate_mode_actions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        add_actions(self.tools, tool)
        self.canvas.menus[0].clear()
        add_actions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (self.actions.create,) if self.beginner()\
            else (self.actions.createMode, self.actions.editMode)
        add_actions(self.menus.edit, actions + self.actions.editMenu)

    def set_beginner(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.beginner)

    def set_advanced(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.advanced)

    # ---- Undo / redo ------------------------------------------------------------------------
    # Every image keeps its own history of box snapshots (taken right before each edit), so
    # going back to an earlier image can still undo there. Kept in memory for this session only.
    UNDO_STEPS = 100    # per image
    UNDO_IMAGES = 300   # images with a history; the least recently edited are dropped first

    def _snapshot(self, shapes=None):
        shapes = self.canvas.shapes if shapes is None else shapes
        return [dict(label=s.label, points=[(p.x(), p.y()) for p in s.points],
                     confidence=s.confidence, difficult=s.difficult, source=s.source,
                     line_color=s.line_color.getRgb(), fill_color=s.fill_color.getRgb(),
                     visible=self.canvas.visible.get(s, True))
                for s in shapes]

    @staticmethod
    def _same_shapes(a, b, tol=1.5):
        """Snapshot equality allowing the sub-pixel rounding a save + reload introduces."""
        if len(a) != len(b):
            return False
        for s, t in zip(a, b):
            if s['label'] != t['label'] or s['confidence'] != t['confidence'] or len(s['points']) != len(t['points']):
                return False
            for (x1, y1), (x2, y2) in zip(s['points'], t['points']):
                if abs(x1 - x2) > tol or abs(y1 - y2) > tol:
                    return False
        return True

    def _history(self, path, create=True):
        h = self.undo_history.get(path)
        if h is None and create:
            h = self.undo_history[path] = {'undo': [], 'redo': [], 'last_kind': None, 'state': None}
            while len(self.undo_history) > self.UNDO_IMAGES:
                self.undo_history.popitem(last=False)
        if h is not None:
            self.undo_history.move_to_end(path)
        return h

    def record_edit(self, kind=None, shapes=None):
        """Call right before changing the boxes. Repeats of the same kind (arrow-key nudges of
        one box in one direction) are merged into a single step."""
        if self.file_path is None or self._restoring_undo:
            return
        h = self._history(self.file_path)
        if kind is None or kind != h['last_kind']:
            h['undo'].append(self._snapshot(shapes))
            del h['undo'][:-self.UNDO_STEPS]
        h['redo'] = []
        h['last_kind'] = kind
        self.update_undo_actions()

    def on_canvas_edit(self, kind):
        if kind.startswith('nudge') and self.canvas.selected_shape is not None:
            kind = '%s-%d' % (kind, id(self.canvas.selected_shape))
        else:
            kind = None  # each drag is its own step
        self.record_edit(kind)

    def _restore_snapshot(self, snap):
        self._restoring_undo = True
        try:
            self.canvas.de_select_shape()
            self.items_to_shapes.clear()
            self.shapes_to_items.clear()
            self.label_list.clear()
            shapes = []
            for d in snap:
                shape = Shape(label=d['label'])
                for x, y in d['points']:
                    shape.add_point(QPointF(x, y))
                shape.close()
                shape.confidence = d['confidence']
                shape.difficult = d['difficult']
                shape.source = d.get('source')
                shape.line_color = QColor(*d['line_color'])
                shape.fill_color = QColor(*d['fill_color'])
                shapes.append(shape)
                self.add_label(shape)
            self.canvas.visible = {}
            self.canvas.load_shapes(shapes)
            for shape, d in zip(shapes, snap):
                if not d['visible']:
                    self.shapes_to_items[shape].setCheckState(Qt.Unchecked)
            self.update_combo_box()
            for action in self.actions.onShapesPresent:
                action.setEnabled(bool(shapes))
            self.shape_selection_changed(False)
            self.set_dirty()
        finally:
            self._restoring_undo = False

    def _undo_redo(self, take, give):
        if self.canvas.drawing() and self.canvas.cancel_drawing():
            return  # Undo while drawing just drops the half-drawn box
        if self.file_path is None:
            return
        h = self._history(self.file_path, create=False)
        if not h:
            return
        current = self._snapshot()
        # Skip steps that changed nothing (e.g. a box grabbed but not really moved)
        while h[take] and h[take][-1] == current:
            h[take].pop()
        if not h[take]:
            self.update_undo_actions()
            return
        h[give].append(current)
        self._restore_snapshot(h[take].pop())
        h['last_kind'] = None
        self.update_undo_actions()
        self.status('Undo' if take == 'undo' else 'Redo')

    def undo_edit(self, _value=False):
        self._undo_redo('undo', 'redo')

    def redo_edit(self, _value=False):
        self._undo_redo('redo', 'undo')

    def update_undo_actions(self):
        h = self._history(self.file_path, create=False) if self.file_path else None
        self.actions.undo.setEnabled(bool(h and h['undo']) or bool(self.canvas.current))
        self.actions.redo.setEnabled(bool(h and h['redo']))

    def _leave_image_for_undo(self):
        """Remember the boxes as they are when leaving an image, to detect outside changes."""
        if self.file_path and self.file_path in self.undo_history:
            h = self.undo_history[self.file_path]
            h['state'] = self._snapshot()
            h['last_kind'] = None

    def _enter_image_for_undo(self):
        """On (re)opening an image: drop its history if the file no longer matches it, e.g.
        edited by another tool or loaded from a different save dir."""
        h = self.undo_history.get(self.file_path)
        if h is not None and h['state'] is not None and not self._same_shapes(h['state'], self._snapshot()):
            del self.undo_history[self.file_path]
        self.update_undo_actions()

    def set_dirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def set_clean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggle_actions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for z in self.actions.lightActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queue_event(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def reset_state(self):
        self._leave_image_for_undo()
        self.items_to_shapes.clear()
        self.shapes_to_items.clear()
        self.label_list.clear()
        self.file_path = None
        self.image_data = None
        self.label_file = None
        self.canvas.reset_state()
        self.label_coordinates.clear()
        self.combo_box.cb.clear()

    def current_item(self):
        items = self.label_list.selectedItems()
        if items:
            return items[0]
        return None

    def add_recent_file(self, file_path):
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        elif len(self.recent_files) >= self.max_recent:
            self.recent_files.pop()
        self.recent_files.insert(0, file_path)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def show_tutorial_dialog(self, browser='default', link=None):
        if link is None:
            link = self.screencast

        if browser.lower() == 'default':
            wb.open(link, new=2)
        elif browser.lower() == 'chrome' and self.os_name == 'Windows':
            if shutil.which(browser.lower()):  # 'chrome' not in wb._browsers in windows
                wb.register('chrome', None, wb.BackgroundBrowser('chrome'))
            else:
                chrome_path="D:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
                if os.path.isfile(chrome_path):
                    wb.register('chrome', None, wb.BackgroundBrowser(chrome_path))
            try:
                wb.get('chrome').open(link, new=2)
            except:
                wb.open(link, new=2)
        elif browser.lower() in wb._browsers:
            wb.get(browser.lower()).open(link, new=2)

    def show_default_tutorial_dialog(self):
        self.show_tutorial_dialog(browser='default')

    def show_info_dialog(self):
        from libs.__init__ import __version__
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)

    def apply_shortcuts(self, custom):
        """Set every action's keys: the default unless changed in the shortcut editor."""
        self.custom_shortcuts = dict((eid, list(keys)) for eid, keys in custom.items()
                                     if eid in self.default_shortcuts)
        for eid, _name, acts in self.shortcut_entries:
            keys = self.custom_shortcuts.get(eid, self.default_shortcuts[eid])
            for a in acts:
                a.setShortcuts([QKeySequence(k) for k in keys])

    def edit_shortcuts(self, _value=False):
        entries = [(eid, name, self.default_shortcuts[eid]) for eid, name, _acts in self.shortcut_entries]
        current = dict((eid, self.custom_shortcuts.get(eid, self.default_shortcuts[eid]))
                       for eid, _name, _acts in self.shortcut_entries)
        dialog = ShortcutsDialog(entries, current, parent=self)
        if dialog.exec_():
            self.apply_shortcuts(dialog.result_keys())
            self.settings[SETTING_SHORTCUTS] = self.custom_shortcuts
            self.settings.save()
            self.status('Keyboard shortcuts updated')

    def show_shortcuts_dialog(self):
        self.show_tutorial_dialog(browser='default', link='https://github.com/tzutalin/labelImg#Hotkeys')

    def create_shape(self):
        assert self.beginner()
        self.canvas.set_editing(False)
        self.actions.create.setEnabled(False)

    def toggle_drawing_sensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        self.update_undo_actions()  # Undo cancels a box being drawn
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.set_editing(True)
            self.canvas.restore_cursor()
            self.actions.create.setEnabled(True)

    def toggle_draw_mode(self, edit=True):
        self.canvas.set_editing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def set_create_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(False)

    def set_edit_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(True)
        self.label_selection_changed()

    def update_file_menu(self):
        curr_file_path = self.file_path

        def exists(filename):
            return os.path.exists(filename)
        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recent_files if f !=
                 curr_file_path and exists(f)]
        for i, f in enumerate(files):
            icon = new_icon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.load_recent, f))
            menu.addAction(action)

    def pop_label_list_menu(self, point):
        self.menus.labelList.exec_(self.label_list.mapToGlobal(point))

    def edit_label(self):
        if not self.canvas.editing():
            return
        item = self.current_item()
        if not item:
            return
        if len(self.label_hist) > 0:
            self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)
        shape = self.items_to_shapes[item]
        # Edit only the class name; the list text may also carry the confidence ('AS 0.87')
        text = self.label_dialog.pop_up(shape.label)
        if text is not None and self.check_label_allowed(text):
            self.set_shape_label(shape, text)

    def set_shape_label(self, shape, text):
        """Change one box's class (undoable)."""
        if text == shape.label:
            return
        self.record_edit()
        shape.label = text
        shape.line_color = generate_color_by_text(text)
        item = self.shapes_to_items[shape]
        item.setText(shape.display_text())
        item.setBackground(generate_color_by_text(text))
        if text not in self.label_hist and self.classes_known:
            self.label_hist.append(text)
        self.set_dirty()
        self.update_combo_box()
        self.canvas.update()

    def class_list_rows(self):
        """Class names for the class list. Without classes.txt boxes keep plain ids, so list
        the ids known so far plus the ones used on this image."""
        if self.classes_known:
            return list(self.label_hist)
        used = set(self.label_hist) | set(str(s.label) for s in self.canvas.shapes)
        return sorted(used, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))

    def refresh_class_list(self):
        rows = self.class_list_rows()
        if rows != getattr(self, '_class_list_rows', None):
            self._class_list_rows = rows
            self.class_list.clear()
            for i, name in enumerate(rows):
                # YOLO id first, as written in the label files
                text = '%d  %s' % (i, name) if self.classes_known else name
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, name)
                item.setBackground(generate_color_by_text(name))
                self.class_list.addItem(item)
            row_h = max(self.class_list.sizeHintForRow(0), 16)
            self.class_list.setMaximumHeight(row_h * 8 + 2 * self.class_list.frameWidth() + 2)
        self.highlight_class_list()

    def highlight_class_list(self):
        """Mark the selected box's class, or the class new boxes get."""
        shape = self.canvas.selected_shape
        if shape is not None:
            name = str(shape.label)
        elif self.use_default_label_checkbox.isChecked():
            name = self.default_label
        else:
            name = None
        rows = getattr(self, '_class_list_rows', [])
        self.class_list.blockSignals(True)
        if name in rows:
            self.class_list.setCurrentRow(rows.index(name))
            self.class_list.scrollToItem(self.class_list.currentItem())
        else:
            self.class_list.setCurrentRow(-1)
            self.class_list.clearSelection()
        self.class_list.blockSignals(False)

    def class_list_clicked(self, item):
        name = item.data(Qt.UserRole)
        shape = self.canvas.selected_shape
        if shape is not None and self.canvas.editing():
            if self.check_label_allowed(name):
                self.set_shape_label(shape, name)
        else:
            # No box selected: this becomes the class of the next boxes, without the dialog
            if name in self.label_hist:
                self.default_label_combo_box.cb.setCurrentIndex(self.label_hist.index(name))
            self.default_label = name
            self.use_default_label_checkbox.setChecked(True)
        self.highlight_class_list()

    # Tzutalin 20160906 : Add file list and dock to move faster
    def file_item_double_clicked(self, item=None):
        self.cur_img_idx = self.m_img_list.index(ustr(item.text()))
        filename = self.m_img_list[self.cur_img_idx]
        if filename:
            self.load_file(filename)

    def _label_txt_path(self, image_path):
        if self.default_save_dir is not None:
            stem = ustr(self.annotation_stem(image_path))
            candidate = stem + TXT_EXT
            if os.path.isfile(candidate):
                return candidate
            basename = os.path.basename(os.path.splitext(image_path)[0])
            fallback = os.path.join(self.default_save_dir, basename) + TXT_EXT
            if os.path.isfile(fallback):
                return fallback
            return None
        candidate = os.path.splitext(image_path)[0] + TXT_EXT
        return candidate if os.path.isfile(candidate) else None

    def _scan_label_file(self, image_path):
        # ids: every box's class id, sorted (repeats kept), for sorting by labels
        stats = {'classes': set(), 'count': 0, 'min_conf': None, 'ids': []}
        txt_path = self._label_txt_path(image_path)
        if not txt_path:
            return stats
        class_list = self.class_list_for(image_path) or []
        try:
            with open(txt_path, 'r') as f:
                for line in f:
                    fields = line.split()
                    if len(fields) < 5:
                        continue
                    stats['count'] += 1
                    try:
                        class_idx = int(fields[0])
                    except ValueError:
                        continue
                    stats['ids'].append(class_idx)
                    if 0 <= class_idx < len(class_list):
                        stats['classes'].add(class_list[class_idx])
                    else:
                        stats['classes'].add(str(class_idx))
                    if len(fields) >= 6:
                        try:
                            conf = float(fields[5])
                        except ValueError:
                            continue
                        if stats['min_conf'] is None or conf < stats['min_conf']:
                            stats['min_conf'] = conf
        except OSError:
            pass
        stats['ids'].sort()
        return stats

    def _build_label_cache(self):
        self.m_img_cache = {p: self._scan_label_file(p) for p in self.m_img_full_list}
        self._refresh_filter_class_menu()
        self.sort_image_list()

    def sort_image_list(self):
        """Put m_img_full_list (and so the filtered list, a / d and 'nearest match') in the chosen
        order. By labels: the sorted class ids of each image compared one by one, so [0, 1] <
        [0, 1, 4] < [0, 5] < [2]; no boxes ([]) first; equal ids by filename. Datasets of a
        multi-dataset folder stay in their order, each sorted on its own, since the same id can
        mean different classes there. Only re-run on open / sort change / Re-sort, not on save,
        so images don't jump around while working through the list."""
        name_order = getattr(self, 'm_img_name_order', None)
        if name_order is None or set(name_order) != set(self.m_img_full_list):
            # Scan order is the filename order (natural sort, datasets in order)
            self.m_img_name_order = name_order = list(self.m_img_full_list)
        if self.sort_combo.currentText() != SORT_BY_LABELS:
            self.m_img_full_list = list(name_order)
            return
        dataset_index = dict((d['images'], i) for i, d in enumerate(self.sub_datasets))
        empty = {'ids': []}

        def key(item):
            pos, path = item
            dataset = self.dataset_for(path)
            return (dataset_index.get(dataset['images'], 0) if dataset else 0,
                    self.m_img_cache.get(path, empty)['ids'], pos)
        self.m_img_full_list = [p for _, p in sorted(enumerate(name_order), key=key)]

    def apply_sort_change(self):
        self.resort_button.setEnabled(self.sort_combo.currentText() == SORT_BY_LABELS)
        if not self.m_img_full_list:
            return
        self.sort_image_list()
        self.apply_filter_change()

    def resort(self):
        """Re-sort button: read all label files again (also picks up outside changes), then sort."""
        if not self.m_img_full_list:
            return
        self._build_label_cache()  # also sorts
        self.apply_filter_change()

    def filter_class_actions(self):
        """The checkable class items of the Class filter menu (not All / None)."""
        return [a for a in self.filter_class_menu.actions() if a.isCheckable()]

    def _refresh_filter_class_menu(self):
        prev_checked = {a.text() for a in self.filter_class_actions() if a.isChecked()}
        classes = set()
        for stats in self.m_img_cache.values():
            classes |= stats['classes']
        self.filter_class_menu.clear()
        if classes:
            self.filter_class_menu.addAction('All', partial(self._set_all_class_filters, True))
            self.filter_class_menu.addAction('None', partial(self._set_all_class_filters, False))
            self.filter_class_menu.addSeparator()
        for cls in sorted(classes):
            act = self.filter_class_menu.addAction(cls)
            act.setCheckable(True)
            if cls in prev_checked:
                act.setChecked(True)
            act.toggled.connect(self._on_class_filter_changed)
        self._update_class_button_label()

    def _set_all_class_filters(self, checked):
        """Tick / untick every class at once, filtering only once."""
        self._rebuild_blocked = True
        try:
            for act in self.filter_class_actions():
                act.setChecked(checked)
        finally:
            self._rebuild_blocked = False
        self._on_class_filter_changed()

    def _update_class_button_label(self):
        n = sum(1 for a in self.filter_class_actions() if a.isChecked())
        self.filter_class_button.setText('All classes' if n == 0 else '%d class%s' % (n, '' if n == 1 else 'es'))

    def _on_class_filter_changed(self, _checked=False):
        self._update_class_button_label()
        self.apply_filter_change()

    def _passes_filter(self, image_path):
        name_q = self.filter_name_edit.text().strip().lower()
        if name_q and name_q not in os.path.basename(image_path).lower():
            return False
        stats = self.m_img_cache.get(image_path, {'classes': set(), 'count': 0, 'min_conf': None})
        if stats['count'] < self.filter_count_min.value() or stats['count'] > self.filter_count_max.value():
            return False
        selected = {a.text() for a in self.filter_class_actions() if a.isChecked()}
        if selected and not (selected & stats['classes']):
            return False
        if self.filter_conf_check.isChecked():
            thr = self.filter_conf_value.value()
            if stats['min_conf'] is None or stats['min_conf'] >= thr:
                return False
        return True

    def rebuild_file_list(self):
        if self._rebuild_blocked:
            return
        old_current = self.file_path
        self.m_img_list = [p for p in self.m_img_full_list if self._passes_filter(p)]
        self.img_count = len(self.m_img_list)
        self.file_list_widget.clear()
        for p in self.m_img_list:
            self.file_list_widget.addItem(QListWidgetItem(p))
        self.filter_stats_label.setText('%d / %d' % (len(self.m_img_list), len(self.m_img_full_list)))
        if old_current and old_current in self.m_img_list:
            self.cur_img_idx = self.m_img_list.index(old_current)
            self.select_file_item(self.cur_img_idx)
        elif self.m_img_list:
            self.cur_img_idx = min(self.cur_img_idx, len(self.m_img_list) - 1)
        else:
            self.cur_img_idx = 0

    def _nearest_in_filter(self, image_path, direction=1):
        """Closest image passing the filter, by position in the unfiltered list.
        direction=1: first match after image_path, else the last one before it.
        direction=-1: last match before image_path only."""
        if not self.m_img_list:
            return None
        if image_path not in self.m_img_full_list:
            return self.m_img_list[0] if direction > 0 else None
        pos = self.m_img_full_list.index(image_path)
        allowed = set(self.m_img_list)
        after = [p for p in self.m_img_full_list[pos + 1:] if p in allowed]
        before = [p for p in self.m_img_full_list[:pos] if p in allowed]
        if direction > 0:
            return after[0] if after else (before[-1] if before else None)
        return before[-1] if before else None

    def apply_filter_change(self):
        """Rebuild the list after a filter change; if the current image dropped out of it,
        move to the nearest matching image (the next one, else the previous one)."""
        if self._rebuild_blocked:
            return
        self.rebuild_file_list()
        current = self.file_path
        if current is None:
            if self.m_img_list:
                self.cur_img_idx = 0
                self.load_file(self.m_img_list[0])
            return
        if current in self.m_img_list:
            return
        if not self.m_img_list:
            self.statusBar().showMessage('No images match the filter')
            return
        # Unsaved edits first: autosave, or ask (Cancel keeps this image)
        if self.dirty:
            if self.auto_saving.isChecked() and self.default_save_dir is not None:
                self.save_file()  # also rebuilds the list; the edit may make this image match
                if self.file_path in self.m_img_list:
                    return
            elif not self.may_continue():
                return
        target = self._nearest_in_filter(current, 1)
        if target:
            self.cur_img_idx = self.m_img_list.index(target)
            self.load_file(target)

    def select_file_item(self, index):
        """Highlight the current image in the file list and scroll it into view."""
        item = self.file_list_widget.item(index)
        if item is None:
            return
        # setCurrentItem also clears the old selection and moves keyboard focus row
        self.file_list_widget.setCurrentItem(item)
        # Keep 2 rows of context: scrolling starts when the current row reaches the third from
        # the bottom (or top), so the next images are always in view
        count = self.file_list_widget.count()
        for neighbour in (min(index + 2, count - 1), max(index - 2, 0), index):
            self.file_list_widget.scrollToItem(self.file_list_widget.item(neighbour),
                                               QAbstractItemView.EnsureVisible)

    def clear_filters(self):
        self._rebuild_blocked = True
        try:
            self.filter_name_edit.clear()
            self.filter_count_min.setValue(0)
            self.filter_count_max.setValue(9999)
            self.filter_conf_check.setChecked(False)
            for act in self.filter_class_actions():
                act.setChecked(False)
            self._update_class_button_label()
        finally:
            self._rebuild_blocked = False
        self.apply_filter_change()

    # Add chris
    def button_state(self, item=None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.current_item()
        if not item:  # If not selected Item, take the first one
            item = self.label_list.item(self.label_list.count() - 1)

        difficult = self.diffc_button.isChecked()

        try:
            shape = self.items_to_shapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.set_dirty()
            else:  # User probably changed item visibility
                self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)
        except:
            pass

    # React to canvas signals.
    def shape_selection_changed(self, selected=False):
        if self._no_selection_slot:
            self._no_selection_slot = False
        else:
            shape = self.canvas.selected_shape
            if shape:
                self.shapes_to_items[shape].setSelected(True)
            else:
                self.label_list.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)
        self.highlight_class_list()

    def add_label(self, shape):
        shape.paint_label = self.display_label_option.isChecked()
        item = HashableQListWidgetItem(shape.display_text())
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setBackground(generate_color_by_text(shape.label))
        self.items_to_shapes[item] = shape
        self.shapes_to_items[shape] = item
        self.label_list.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)
        self.update_combo_box()

    def remove_label(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapes_to_items[shape]
        self.label_list.takeItem(self.label_list.row(item))
        del self.shapes_to_items[shape]
        del self.items_to_shapes[item]
        self.update_combo_box()

    def load_labels(self, shapes):
        s = []
        for shape_data in shapes:
            # YOLO shapes carry a 6th item (confidence); Pascal VOC / CreateML ones have 5
            label, points, line_color, fill_color, difficult = shape_data[:5]
            shape = Shape(label=label)
            shape.confidence = shape_data[5] if len(shape_data) > 5 else None
            any_snapped = False
            for x, y in points:

                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snap_point_to_canvas(x, y)
                if snapped:
                    self.set_dirty()
                    any_snapped = True

                shape.add_point(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            # YOLO: remember the original line, written back as is while the box is unchanged
            if len(shape_data) > 6 and shape_data[6] and not any_snapped:
                shape.source = (shape_data[6], label, [(p.x(), p.y()) for p in shape.points],
                                shape.confidence)
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                shape.line_color = generate_color_by_text(label)

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                shape.fill_color = generate_color_by_text(label)

            self.add_label(shape)
        self.update_combo_box()
        self.canvas.load_shapes(s)

    def update_combo_box(self):
        # Get the unique labels and add them to the Combobox.
        # Class names, not the list text (which may include the confidence)
        items_text_list = [str(self.items_to_shapes[self.label_list.item(i)].label)
                           for i in range(self.label_list.count())]

        # First row shows all the labels (see combo_selection_changed)
        unique_text_list = [ALL_CLASSES_ITEM] + sorted(set(items_text_list))

        self.combo_box.update_items(unique_text_list)
        self.refresh_class_list()

    def save_labels(self, annotation_file_path):
        annotation_file_path = ustr(annotation_file_path)
        if self.label_file is None:
            self.label_file = LabelFile()
            self.label_file.verified = self.canvas.verified

        def format_shape(s):
            return dict(label=s.label,
                        line_color=s.line_color.getRgb(),
                        fill_color=s.fill_color.getRgb(),
                        points=[(p.x(), p.y()) for p in s.points],
                        # add chris
                        difficult=s.difficult,
                        confidence=s.confidence,
                        source_line=s.source_line())

        shapes = [format_shape(shape) for shape in self.canvas.shapes]
        # Can add different annotation formats here
        try:
            if self.label_file_format == LabelFileFormat.PASCAL_VOC:
                if annotation_file_path[-4:].lower() != ".xml":
                    annotation_file_path += XML_EXT
                self.label_file.save_pascal_voc_format(annotation_file_path, shapes, self.file_path, self.image_data,
                                                       self.line_color.getRgb(), self.fill_color.getRgb())
            elif self.label_file_format == LabelFileFormat.YOLO:
                if annotation_file_path[-4:].lower() != ".txt":
                    annotation_file_path += TXT_EXT
                self.label_file.save_yolo_format(annotation_file_path, shapes, self.file_path, self.image_data, self.label_hist,
                                                 self.line_color.getRgb(), self.fill_color.getRgb(),
                                                 # Unknown class names: keep ids, don't invent a classes.txt
                                                 classes_file=self.classes_file_path if self.classes_known else False)
            elif self.label_file_format == LabelFileFormat.CREATE_ML:
                if annotation_file_path[-5:].lower() != ".json":
                    annotation_file_path += JSON_EXT
                self.label_file.save_create_ml_format(annotation_file_path, shapes, self.file_path, self.image_data,
                                                      self.label_hist, self.line_color.getRgb(), self.fill_color.getRgb())
            else:
                self.label_file.save(annotation_file_path, shapes, self.file_path, self.image_data,
                                     self.line_color.getRgb(), self.fill_color.getRgb())
            print('Image:{0} -> Annotation:{1}'.format(self.file_path, annotation_file_path))
            return True
        except LabelFileError as e:
            self.error_message(u'Error saving label data', u'<b>%s</b>' % e)
            return False

    def copy_selected_shape(self):
        if self.canvas.selected_shape is not None:
            self.record_edit()
        shape = self.canvas.copy_selected_shape()
        if shape is None:
            return
        self.add_label(shape)
        # fix copy and delete
        self.shape_selection_changed(True)

    def combo_selection_changed(self, index):
        text = self.combo_box.cb.itemText(index)
        for i in range(self.label_list.count()):
            if index <= 0:  # 'All classes' row (or the box being cleared)
                self.label_list.item(i).setCheckState(2)
            elif text != self.items_to_shapes[self.label_list.item(i)].label:
                self.label_list.item(i).setCheckState(0)
            else:
                self.label_list.item(i).setCheckState(2)

    def default_label_combo_selection_changed(self, index):
        # index is -1 when the combo box is cleared (e.g. Open Dir with no classes.txt)
        if 0 <= index < len(self.label_hist):
            self.default_label = self.label_hist[index]
        if hasattr(self, 'class_list'):
            self.highlight_class_list()

    def label_selection_changed(self):
        item = self.current_item()
        if item and self.canvas.editing():
            self._no_selection_slot = True
            self.canvas.select_shape(self.items_to_shapes[item])
            shape = self.items_to_shapes[item]
            # Add Chris
            self.diffc_button.setChecked(shape.difficult)

    def label_item_changed(self, item):
        shape = self.items_to_shapes[item]
        if item.text() != shape.display_text():
            # Text was changed directly in the list; take it as the new class name
            self.record_edit()
            shape.label = item.text()
            shape.line_color = generate_color_by_text(shape.label)
            self.set_dirty()
        else:  # User probably changed item visibility
            self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)

    # Callback functions:
    def new_shape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if not self.use_default_label_checkbox.isChecked():
            if len(self.label_hist) > 0:
                self.label_dialog = LabelDialog(
                    parent=self, list_item=self.label_hist)

            # Sync single class mode from PR#106
            if self.single_class_mode.isChecked() and self.lastLabel:
                text = self.lastLabel
            else:
                text = self.label_dialog.pop_up(text=self.prev_label_text)
                self.lastLabel = text
        else:
            text = self.default_label

        if text is not None and not self.check_label_allowed(text):
            text = None

        # Add Chris
        self.diffc_button.setChecked(False)
        if text is not None:
            self.prev_label_text = text
            self.record_edit(shapes=self.canvas.shapes[:-1])  # state before the new box
            generate_color = generate_color_by_text(text)
            shape = self.canvas.set_last_label(text, generate_color, generate_color)
            # Drawn by hand: full confidence (YOLO 6th column). Existing boxes keep theirs.
            shape.confidence = NEW_BOX_CONFIDENCE
            self.add_label(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.set_editing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.set_dirty()

            if text not in self.label_hist:
                self.label_hist.append(text)
                self.refresh_class_list()
        else:
            # self.canvas.undoLastLine()
            self.canvas.reset_all_lines()

    def scroll_request(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scroll_bars[orientation]
        bar.setValue(int(bar.value() + bar.singleStep() * units))

    def set_zoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.MANUAL_ZOOM
        # Arithmetic on scaling factor often results in float
        # Convert to int to avoid type errors
        self.zoom_widget.setValue(int(value))

    def add_zoom(self, increment=10):
        self.set_zoom(self.zoom_widget.value() + increment)

    def zoom_request(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scroll_bars[Qt.Horizontal]
        v_bar = self.scroll_bars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scroll_area.width()
        h = self.scroll_area.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta // (8 * 15)
        scale = 10
        self.add_zoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = int(h_bar.value() + move_x * d_h_bar_max)
        new_v_bar_value = int(v_bar.value() + move_y * d_v_bar_max)

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)

    def zoom_at(self, factor, pos):
        """Zoom by factor, keeping the image point under pos (canvas coordinates) under the cursor."""
        if self.image.isNull():
            return
        old_scale = self.canvas.scale
        image_point = self.canvas.transform_pos(pos)
        h_bar = self.scroll_bars[Qt.Horizontal]
        v_bar = self.scroll_bars[Qt.Vertical]
        # Cursor position inside the visible viewport (canvas is shifted by the scroll values)
        view_x, view_y = pos.x() - h_bar.value(), pos.y() - v_bar.value()

        current = self.zoom_widget.value()
        lo, hi = self.zoom_widget.minimum(), self.zoom_widget.maximum()
        # Smooth scrolling / pinch send many tiny steps: accumulate them in a float so they add
        # up to the same zoom as whole wheel notches instead of rounding away (or to 1% each)
        if self._zoom_exact is None or int(round(self._zoom_exact)) != current:
            self._zoom_exact = float(current)
        self._zoom_exact = max(lo, min(hi, self._zoom_exact * factor))
        target = int(round(self._zoom_exact))
        if target == current:
            return
        if target <= lo and lo < 100:
            # Zoomed all the way out = whole image in view: back to Fit Window, which also
            # keeps fitting when the window is resized
            self._zoom_exact = None
            self.actions.fitWindow.setChecked(True)
            self.set_fit_window(True)
            return
        self.set_zoom(target)

        if self.canvas.scale == old_scale:
            return
        # Let the scroll area apply the new canvas size first (scroll bar ranges, and the viewport
        # shrinking when a scroll bar appears); otherwise the values below get clamped
        QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        # Where the same image point is on the resized canvas, then scroll it back under the cursor
        new_pos = (image_point + self.canvas.offset_to_center()) * self.canvas.scale
        h_bar.setValue(int(round(new_pos.x() - view_x)))
        v_bar.setValue(int(round(new_pos.y() - view_y)))

    def pan_request(self, dx, dy):
        """Drag panning: the image follows the mouse 1:1."""
        h_bar = self.scroll_bars[Qt.Horizontal]
        v_bar = self.scroll_bars[Qt.Vertical]
        h_bar.setValue(h_bar.value() - dx)
        v_bar.setValue(v_bar.value() - dy)

    def update_min_zoom(self):
        """Smallest zoom = the whole image in view (Fit Window), but never above 100% so small
        images can still be seen at their real size."""
        if self.canvas.pixmap.isNull() or self.canvas.pixmap.width() == 0:
            self.zoom_widget.setMinimum(1)
            return
        lowest = max(1, min(100, int(100 * self.scale_fit_window())))
        if lowest == self.zoom_widget.minimum():
            return
        if self.zoom_widget.value() < lowest and self.zoom_mode == self.MANUAL_ZOOM:
            # e.g. window made bigger while zoomed out: snap to fitting the whole image
            self.zoom_mode = self.FIT_WINDOW
            self.actions.fitWindow.setChecked(True)
        self.zoom_widget.setMinimum(lowest)  # also raises the current value if it was below

    def eventFilter(self, obj, event):
        # The image area also changes size when the side docks are resized, not just the window
        if obj is self.scroll_area and event.type() == QEvent.Resize:
            if not self.image.isNull():
                self.update_min_zoom()
                if self.zoom_mode != self.MANUAL_ZOOM:
                    self.adjust_scale()  # refit with the new size (MainWindow.resizeEvent runs too early)
        return super(MainWindow, self).eventFilter(obj, event)

    def light_request(self, delta):
        self.add_light(5*delta // (8 * 15))

    def set_fit_window(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoom_mode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def set_fit_width(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def set_light(self, value):
        self.actions.lightOrg.setChecked(int(value) == 50)
        # Arithmetic on scaling factor often results in float
        # Convert to int to avoid type errors
        self.light_widget.setValue(int(value))

    def add_light(self, increment=10):
        self.set_light(self.light_widget.value() + increment)

    def toggle_polygons(self, value):
        for item, shape in self.items_to_shapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def load_file(self, file_path=None):
        """Load the specified file, or the last opened file if None."""
        self.reset_state()
        self.canvas.setEnabled(False)
        if file_path is None:
            file_path = self.settings.get(SETTING_FILENAME)
        # Make sure that filePath is a regular python string, rather than QString
        file_path = ustr(file_path)

        # Fix bug: An  index error after select a directory when open a new file.
        unicode_file_path = ustr(file_path)
        unicode_file_path = os.path.abspath(unicode_file_path)
        # Multi-dataset mode: use the classes.txt of the dataset this image belongs to
        self.activate_dataset_for(unicode_file_path)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicode_file_path and self.file_list_widget.count() > 0:
            if unicode_file_path in self.m_img_list:
                index = self.m_img_list.index(unicode_file_path)
                self.select_file_item(index)
            elif unicode_file_path in self.m_img_full_list:
                # In the folder but hidden by the filter: keep the list, just select nothing
                self.file_list_widget.setCurrentItem(None)
            else:
                self.file_list_widget.clear()
                self.m_img_list.clear()
                self.m_img_full_list.clear()
                self.m_img_cache.clear()

        if unicode_file_path and os.path.exists(unicode_file_path):
            if LabelFile.is_label_file(unicode_file_path):
                try:
                    self.label_file = LabelFile(unicode_file_path)
                except LabelFileError as e:
                    self.error_message(u'Error opening file',
                                       (u"<p><b>%s</b></p>"
                                        u"<p>Make sure <i>%s</i> is a valid label file.")
                                       % (e, unicode_file_path))
                    self.status("Error reading %s" % unicode_file_path)
                    
                    return False
                self.image_data = self.label_file.image_data
                self.line_color = QColor(*self.label_file.lineColor)
                self.fill_color = QColor(*self.label_file.fillColor)
                self.canvas.verified = self.label_file.verified
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.image_data = read(unicode_file_path, None)
                self.label_file = None
                self.canvas.verified = False

            if isinstance(self.image_data, QImage):
                image = self.image_data
            else:
                image = QImage.fromData(self.image_data)
            if image.isNull():
                self.error_message(u'Error opening file',
                                   u"<p>Make sure <i>%s</i> is a valid image file." % unicode_file_path)
                self.status("Error reading %s" % unicode_file_path)
                return False
            self.status("Loaded %s" % os.path.basename(unicode_file_path))
            self.image = image
            self.file_path = unicode_file_path
            self.canvas.load_pixmap(QPixmap.fromImage(image))
            if self.label_file:
                self.load_labels(self.label_file.shapes)
            self.set_clean()
            self.canvas.setEnabled(True)
            self.update_min_zoom()
            self.adjust_scale(initial=True)
            self.paint_canvas()
            self.add_recent_file(self.file_path)
            self.toggle_actions(True)
            self.show_bounding_box_from_annotation_file(self.file_path)
            self._enter_image_for_undo()  # after the boxes are loaded from the file

            counter = self.counter_str()
            self.setWindowTitle(__appname__ + ' ' + file_path + ' ' + counter)

            # Default : select last item if there is at least one item
            if self.label_list.count():
                self.label_list.setCurrentItem(self.label_list.item(self.label_list.count() - 1))
                self.label_list.item(self.label_list.count() - 1).setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False

    def counter_str(self):
        """
        Converts image counter to string representation.
        """
        return '[{} / {}]'.format(self.cur_img_idx + 1, self.img_count)

    @staticmethod
    def find_sub_datasets(root):
        """Direct sub-folders of root laid out as <name>/images (+ labels/, classes.txt)."""
        datasets = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return datasets
        for name in names:
            sub = os.path.join(root, name)
            images = os.path.join(sub, 'images')
            if not name.startswith('.') and os.path.isdir(images):
                datasets.append({'name': name,
                                 'root': os.path.abspath(sub),
                                 'images': os.path.abspath(images),
                                 'labels': os.path.abspath(os.path.join(sub, 'labels')),
                                 'classes_file': os.path.abspath(os.path.join(sub, 'classes.txt')),
                                 'classes': None,
                                 'classes_known': True})
        return datasets

    def dataset_for(self, image_path):
        """The sub-dataset an image belongs to in multi-dataset mode, else None."""
        if not self.sub_datasets or not image_path:
            return None
        path = os.path.abspath(image_path)
        for dataset in self.sub_datasets:
            if path.startswith(dataset['images'] + os.sep):
                return dataset
        return None

    def class_list_for(self, image_path):
        """Class list used to map YOLO class ids for this image."""
        dataset = self.dataset_for(image_path)
        if dataset is not None:
            return dataset['classes']
        return self.label_hist

    def activate_dataset_for(self, image_path):
        """Switch label_hist / classes.txt to the sub-dataset of this image (multi-dataset mode)."""
        dataset = self.dataset_for(image_path)
        if dataset is None or dataset is self._active_dataset:
            return
        self._active_dataset = dataset
        # Shared list object: classes added while labelling go straight into this dataset's list
        self.set_class_list(dataset, dataset['classes'], dataset['classes_known'])

    def set_class_list(self, dataset, classes, known):
        """Use this dataset's class list for reading, saving, the label dialog and the status bar."""
        self.label_hist = classes
        self.classes_file_path = dataset['classes_file']
        self.classes_known = known
        self._current_classes = dataset
        self.default_label_combo_box.cb.blockSignals(True)
        self.default_label_combo_box.cb.clear()
        self.default_label_combo_box.cb.addItems(self.label_hist)
        self.default_label_combo_box.cb.blockSignals(False)
        if self.default_label not in self.label_hist:
            self.default_label = self.label_hist[0] if self.label_hist else ''
        else:
            self.default_label_combo_box.cb.setCurrentIndex(self.label_hist.index(self.default_label))
        self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)
        self.update_classes_status()
        self.refresh_class_list()

    def check_label_allowed(self, text):
        """Without classes.txt, ids are kept as numbers; a new class name would get an id that may
        already be used by existing labels, so only numeric ids are accepted then."""
        if self.classes_known or text.isdigit():
            return True
        self.error_message(u'No classes.txt',
                           u'%s has labels but no classes.txt, so class ids are kept as numbers.<br>'
                           u'Enter a class id (e.g. 0, 1, 2), or reopen the folder and choose a classes.txt.'
                           % (self._current_classes or {}).get('name', 'This dataset'))
        return False

    def update_classes_status(self):
        dataset = self._current_classes
        if dataset is None:
            text = 'Classes: none (open a dataset)'
        elif not dataset['classes_known']:
            text = 'Classes: %s has no classes.txt, ids shown as numbers' % dataset['name']
        elif os.path.isfile(dataset['classes_file']):
            text = 'Classes: %s/classes.txt (%d)' % (dataset['name'], len(self.label_hist))
        else:
            text = 'Classes: %s, new list (%d), classes.txt written on first save' % (dataset['name'], len(self.label_hist))
        self.classes_status_label.setText(text)

    def annotation_stem(self, image_path):
        """Annotation path (without extension) for an image under default_save_dir.

        The image's sub-folder (relative to the opened image dir) is mirrored, so
        images/x/1.jpg -> labels/x/1 and same-named images in different sub-folders
        don't share one label file.
        """
        stem = os.path.splitext(os.path.basename(image_path))[0]
        dataset = self.dataset_for(image_path)
        if dataset is not None:
            # Multi-dataset mode: <dataset>/images/<sub>/x.jpg -> <dataset>/labels/<sub>/x
            rel = os.path.relpath(os.path.dirname(os.path.abspath(image_path)), dataset['images'])
            sub_dir = '' if rel == os.curdir else rel
            return os.path.join(dataset['labels'], sub_dir, stem)
        sub_dir = ''
        if self.dir_name:
            rel = os.path.relpath(os.path.dirname(os.path.abspath(image_path)), os.path.abspath(self.dir_name))
            if rel != os.curdir and not rel.startswith(os.pardir):
                sub_dir = rel
        return os.path.join(self.default_save_dir, sub_dir, stem)

    def show_bounding_box_from_annotation_file(self, file_path):
        if self.default_save_dir is not None:
            # Mirrored path first, then the old flat layout (save_dir/<basename>) for existing datasets
            basename = os.path.basename(os.path.splitext(file_path)[0])
            for stem in (self.annotation_stem(file_path), os.path.join(self.default_save_dir, basename)):
                xml_path = stem + XML_EXT
                txt_path = stem + TXT_EXT
                json_path = stem + JSON_EXT

                """Annotation file priority:
                PascalXML > YOLO
                """
                if os.path.isfile(xml_path):
                    self.load_pascal_xml_by_filename(xml_path)
                    break
                elif os.path.isfile(txt_path):
                    self.load_yolo_txt_by_filename(txt_path)
                    break
                elif os.path.isfile(json_path):
                    self.load_create_ml_json_by_filename(json_path, file_path)
                    break

        else:
            xml_path = os.path.splitext(file_path)[0] + XML_EXT
            txt_path = os.path.splitext(file_path)[0] + TXT_EXT
            json_path = os.path.splitext(file_path)[0] + JSON_EXT

            if os.path.isfile(xml_path):
                self.load_pascal_xml_by_filename(xml_path)
            elif os.path.isfile(txt_path):
                self.load_yolo_txt_by_filename(txt_path)
            elif os.path.isfile(json_path):
                self.load_create_ml_json_by_filename(json_path, file_path)
            

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoom_mode != self.MANUAL_ZOOM:
            self.adjust_scale()
        super(MainWindow, self).resizeEvent(event)

    def paint_canvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoom_widget.value()
        self.canvas.overlay_color = self.light_widget.color()
        # (Display labels size their text by the box now, see Shape.paint)
        self.canvas.label_font_size = 0.02 * max(self.image.width(), self.image.height())
        self.canvas.adjustSize()
        self.canvas.update()

    def adjust_scale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoom_mode]()
        self.zoom_widget.setValue(int(100 * value))

    def scale_fit_window(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scale_fit_width(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.may_continue():
            event.ignore()
        settings = self.settings
        # If it loads images from dir, don't load it at the beginning
        if self.dir_name is None:
            settings[SETTING_FILENAME] = self.file_path if self.file_path else ''
        else:
            settings[SETTING_FILENAME] = ''

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.line_color
        settings[SETTING_FILL_COLOR] = self.fill_color
        settings[SETTING_RECENT_FILES] = self.recent_files
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.default_save_dir and os.path.exists(self.default_save_dir):
            settings[SETTING_SAVE_DIR] = ustr(self.default_save_dir)
        else:
            settings[SETTING_SAVE_DIR] = ''

        if self.last_open_dir and os.path.exists(self.last_open_dir):
            settings[SETTING_LAST_OPEN_DIR] = self.last_open_dir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ''

        settings[SETTING_AUTO_SAVE] = self.auto_saving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.single_class_mode.isChecked()
        settings[SETTING_PAINT_LABEL] = self.display_label_option.isChecked()
        settings[SETTING_DRAW_SQUARE] = self.draw_squares_option.isChecked()
        settings[SETTING_LABEL_FILE_FORMAT] = self.label_file_format
        settings[SETTING_SORT_MODE] = self.sort_combo.currentText()
        settings.save()

    def load_recent(self, filename):
        if self.may_continue():
            self.load_file(filename)

    def scan_all_images(self, folder_path):
        extensions = ['.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        images = []

        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relative_path = os.path.join(root, file)
                    path = ustr(os.path.abspath(relative_path))
                    images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images

    def change_save_dir_dialog(self, _value=False):
        if self.default_save_dir is not None:
            path = ustr(self.default_save_dir)
        else:
            path = '.'

        dir_path = ustr(QFileDialog.getExistingDirectory(self,
                                                         '%s - Save annotations to the directory' % __appname__, path,  QFileDialog.ShowDirsOnly
                                                         | QFileDialog.DontResolveSymlinks))

        if dir_path is not None and len(dir_path) > 1:
            self.default_save_dir = dir_path
            if self.sub_datasets:
                # A manually chosen save dir replaces the per-dataset labels/ folders;
                # classes.txt then goes next to the saved label files as usual
                self.sub_datasets = []
                self._active_dataset = None
                # Keep the current dataset's class list, but write classes.txt next to the labels
                if self._current_classes is not None:
                    self._current_classes = dict(self._current_classes, classes_file=os.path.join(
                        dir_path, 'classes.txt'))
                    self.classes_file_path = self._current_classes['classes_file']
                    self.update_classes_status()

        # Reload the current image so its old shapes are cleared before reading from the new dir.
        # With unsaved edits, keep them on screen instead so they can be saved into the new dir.
        if self.file_path and not self.dirty:
            self.load_file(self.file_path)
        if self.m_img_full_list:
            self._build_label_cache()
            self.rebuild_file_list()

        self.statusBar().showMessage('%s . Annotation will be saved to %s' %
                                     ('Change saved folder', self.default_save_dir))
        self.statusBar().show()


    def open_annotation_dialog(self, _value=False):
        if self.file_path is None:
            self.statusBar().showMessage('Please select image first')
            self.statusBar().show()
            return

        path = os.path.dirname(ustr(self.file_path))\
            if self.file_path else '.'
        if self.label_file_format == LabelFileFormat.PASCAL_VOC:
            filters = "Open Annotation XML file (%s)" % ' '.join(['*.xml'])
            filename = ustr(QFileDialog.getOpenFileName(self, '%s - Choose a xml file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]
            self.load_pascal_xml_by_filename(filename)

        elif self.label_file_format == LabelFileFormat.CREATE_ML:
            
            filters = "Open Annotation JSON file (%s)" % ' '.join(['*.json'])
            filename = ustr(QFileDialog.getOpenFileName(self, '%s - Choose a json file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]

            self.load_create_ml_json_by_filename(filename, self.file_path)         
        

    def open_dir_dialog(self, _value=False, dir_path=None, silent=False):
        if not self.may_continue():
            return

        if dir_path:
            default_open_dir_path = dir_path
        elif self.last_open_dir and os.path.exists(self.last_open_dir):
            default_open_dir_path = self.last_open_dir
        else:
            default_open_dir_path = os.path.dirname(self.file_path) if self.file_path else '.'
        if silent != True:
            target_dir_path = ustr(QFileDialog.getExistingDirectory(self,
                                                                    '%s - Open Directory' % __appname__, default_open_dir_path,
                                                                    QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks))
        else:
            target_dir_path = ustr(default_open_dir_path)

        if not target_dir_path:
            return

        # Leave any previous multi-dataset session
        # Folder of datasets: target/<name>/{images,labels,classes.txt}
        if not os.path.isdir(os.path.join(target_dir_path, 'images')):
            datasets = self.find_sub_datasets(target_dir_path)
            if datasets:
                if not self.resolve_classes(datasets, target_dir_path):
                    return  # cancelled: keep whatever was open before
                self._reset_multi_dataset()
                self.open_multi_dataset_dir(target_dir_path, datasets)
                return

        # Support dataset layout: dataset_dir/{images,labels,classes.txt}; otherwise labels sit next to images
        root = os.path.abspath(target_dir_path)
        images_subdir = os.path.join(root, 'images')
        if os.path.isdir(images_subdir):
            import_path = images_subdir
            labels_dir = os.path.join(root, 'labels')
        else:
            import_path = root
            labels_dir = root
        # classes.txt always lives at the dataset root, so saves don't create labels/classes.txt
        dataset = {'name': os.path.basename(root) or root, 'root': root, 'images': import_path,
                   'labels': labels_dir, 'classes_file': os.path.join(root, 'classes.txt'),
                   'classes': None, 'classes_known': True}
        if not self.resolve_classes([dataset], target_dir_path):
            return
        self._reset_multi_dataset()
        os.makedirs(labels_dir, exist_ok=True)
        self.default_save_dir = labels_dir
        self.set_class_list(dataset, dataset['classes'], dataset['classes_known'])

        self.last_open_dir = target_dir_path
        # default_save_dir is already set, so load_file() inside import_dir_images() reads the
        # first image's annotations; loading them again here would duplicate the label list
        self.import_dir_images(import_path)

    def _reset_multi_dataset(self):
        self.sub_datasets = []
        self.multi_root = None
        self._active_dataset = None

    def resolve_classes(self, datasets, start_dir):
        """Fill each dataset's 'classes' from its classes.txt, asking the user when it's missing.

        Returns False if the user cancels. Never falls back to a built-in list: YOLO files only store
        class ids, so guessing the list would silently relabel boxes.
        """
        missing = []
        for dataset in datasets:
            # Older datasets kept classes.txt inside labels/
            classes = read_classes_file(dataset['classes_file'])
            if classes is None:
                classes = read_classes_file(os.path.join(dataset['labels'], 'classes.txt'))
            dataset['classes'] = classes
            dataset['classes_known'] = True
            if classes is None:
                missing.append(dataset)
        if not missing:
            return True

        sources = [{'name': d['name'], 'classes': d['classes']} for d in datasets if d['classes']]
        info = [{'name': d['name'], 'label_count': count_label_files(d['labels']) if os.path.isdir(d['labels']) else 0}
                for d in missing]
        choice = ClassesDialog(info, sources, start_dir=start_dir, parent=self).get_choice()
        if choice is None:
            return False
        mode, classes = choice
        labelled = [i for i in info if i['label_count']]
        if mode == MODE_EMPTY and labelled:
            answer = QMessageBox.warning(
                self, 'No classes.txt',
                'Already labelled: %s.\n\nWithout a classes.txt their classes are shown as numbers '
                '(0, 1, 2 ...) and saved with the same ids. No classes.txt will be written for them.\n\n'
                'Continue?' % ', '.join('%s (%d files)' % (i['name'], i['label_count']) for i in labelled),
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Ok:
                return False
        for dataset, dataset_info in zip(missing, info):
            if mode == MODE_EMPTY:
                dataset['classes'] = []
                # Existing labels can't be mapped to names: keep their ids as plain numbers and
                # don't create a classes.txt that would claim a meaning for them
                dataset['classes_known'] = dataset_info['label_count'] == 0
            else:
                dataset['classes'] = list(classes)
                write_classes_file(dataset['classes_file'], dataset['classes'])
        return True

    def open_multi_dataset_dir(self, root, datasets):
        """Open every <root>/<name>/images as one list; each dataset keeps its own labels/ and classes.txt."""
        for dataset in datasets:
            os.makedirs(dataset['labels'], exist_ok=True)

        self.sub_datasets = datasets
        self.multi_root = os.path.abspath(root)
        # Non-None so saving goes through annotation_stem(), which picks each dataset's labels/
        self.default_save_dir = self.multi_root
        self.last_open_dir = root
        self.import_dir_images(self.multi_root)

        message = 'Opened %d datasets: %s' % (len(datasets), ', '.join(os.path.basename(d['root']) for d in datasets))
        if len({tuple(d['classes']) for d in datasets}) > 1:
            message += '  |  classes.txt differs between datasets; each dataset uses its own'
        self.statusBar().showMessage(message)
        self.statusBar().show()

    def statistics_datasets(self):
        """Dataset descriptors for the statistics dialog (multi or currently opened single dataset)."""
        if self.sub_datasets:
            return self.sub_datasets
        if self._current_classes is not None:
            return [self._current_classes]
        return []

    def show_statistics(self):
        datasets = self.statistics_datasets()
        if not datasets:
            QMessageBox.information(self, 'Export class statistics', 'Open a dataset folder first.')
            return
        title = os.path.basename(self.multi_root if self.sub_datasets else datasets[0]['root']) or 'statistics'
        start_dir = os.path.expanduser('~/Downloads')
        dialog = StatisticsDialog(title, datasets, start_dir=start_dir, parent=self)
        dialog.exec_()

    def import_dir_images(self, dir_path):
        if not self.may_continue() or not dir_path:
            return

        self.last_open_dir = dir_path
        self.dir_name = dir_path
        self.file_path = None
        self.file_list_widget.clear()
        self.m_img_name_order = None
        if self.sub_datasets and os.path.abspath(dir_path) == self.multi_root:
            # Only the images/ folders of the datasets, in dataset order
            self.m_img_full_list = []
            for dataset in self.sub_datasets:
                self.m_img_full_list.extend(self.scan_all_images(dataset['images']))
        else:
            self.m_img_full_list = self.scan_all_images(dir_path)
        self._build_label_cache()
        self.rebuild_file_list()
        self.statistics_button.setEnabled(bool(self.statistics_datasets()))
        self.open_next_image()

    def verify_image(self, _value=False):
        # Proceeding next image without dialog if having any label
        if self.file_path is not None:
            try:
                self.label_file.toggle_verify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.save_file()
                if self.label_file is not None:
                    self.label_file.toggle_verify()
                else:
                    return

            self.canvas.verified = self.label_file.verified
            self.paint_canvas()
            self.save_file()

    def open_prev_image(self, _value=False):
        # Proceeding prev image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return
        elif not self.may_continue():
            return

        if self.img_count <= 0:
            return

        if self.file_path is None:
            return

        if self.file_path not in self.m_img_list:
            # Current image left the filtered list (e.g. fixed and saved): go to the previous match
            target = self._nearest_in_filter(self.file_path, -1)
            if target:
                self.cur_img_idx = self.m_img_list.index(target)
                self.load_file(target)
            return

        if self.cur_img_idx - 1 >= 0:
            self.cur_img_idx -= 1
            filename = self.m_img_list[self.cur_img_idx]
            if filename:
                self.load_file(filename)

    def open_next_image(self, _value=False):
        # Proceeding next image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return
        elif not self.may_continue():
            return

        if self.img_count <= 0:
            return
        
        if not self.m_img_list:
            return

        filename = None
        if self.file_path is None:
            filename = self.m_img_list[0]
            self.cur_img_idx = 0
        elif self.file_path not in self.m_img_list:
            # Current image left the filtered list: go to the next match after it (only forward)
            pos = self.m_img_full_list.index(self.file_path) if self.file_path in self.m_img_full_list else -1
            allowed = set(self.m_img_list)
            filename = next((p for p in self.m_img_full_list[pos + 1:] if p in allowed), None)
            if filename:
                self.cur_img_idx = self.m_img_list.index(filename)
        else:
            if self.cur_img_idx + 1 < self.img_count:
                self.cur_img_idx += 1
                filename = self.m_img_list[self.cur_img_idx]

        if filename:
            self.load_file(filename)

    def open_file(self, _value=False):
        if not self.may_continue():
            return
        path = os.path.dirname(ustr(self.file_path)) if self.file_path else '.'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename,_ = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.cur_img_idx = 0
            self.img_count = 1
            self.load_file(filename)

    def save_file(self, _value=False):
        if self.default_save_dir is not None and len(ustr(self.default_save_dir)):
            if self.file_path:
                saved_path = ustr(self.annotation_stem(self.file_path))
                os.makedirs(os.path.dirname(saved_path), exist_ok=True)
                self._save_file(saved_path)
        else:
            image_file_dir = os.path.dirname(self.file_path)
            image_file_name = os.path.basename(self.file_path)
            saved_file_name = os.path.splitext(image_file_name)[0]
            saved_path = os.path.join(image_file_dir, saved_file_name)
            self._save_file(saved_path if self.label_file
                            else self.save_file_dialog(remove_ext=False))

    def save_file_as(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._save_file(self.save_file_dialog())

    def save_file_dialog(self, remove_ext=True):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        open_dialog_path = self.current_path()
        dlg = QFileDialog(self, caption, open_dialog_path, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filename_without_extension = os.path.splitext(self.file_path)[0]
        dlg.selectFile(filename_without_extension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            full_file_path = ustr(dlg.selectedFiles()[0])
            if remove_ext:
                return os.path.splitext(full_file_path)[0]  # Return file path without the extension.
            else:
                return full_file_path
        return ''

    def _save_file(self, annotation_file_path):
        if annotation_file_path and self.save_labels(annotation_file_path):
            self.set_clean()
            self.update_classes_status()  # classes.txt may have just been created / grown
            self.statusBar().showMessage('Saved to  %s' % annotation_file_path)
            self.statusBar().show()
            if self.file_path and self.file_path in self.m_img_cache:
                self.m_img_cache[self.file_path] = self._scan_label_file(self.file_path)
                self._refresh_filter_class_menu()
                self.rebuild_file_list()

    def close_file(self, _value=False):
        if not self.may_continue():
            return
        self.reset_state()
        self.set_clean()
        self.toggle_actions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def delete_image(self):
        delete_path = self.file_path
        if delete_path is not None:
            # Annotation files belonging to this image, so they don't linger after the image is gone
            stems = [os.path.splitext(delete_path)[0]]
            if self.default_save_dir:
                stems.append(self.annotation_stem(delete_path))
            label_paths = sorted({stem + ext for stem in stems for ext in (XML_EXT, TXT_EXT, JSON_EXT)
                                  if os.path.isfile(stem + ext)})
            msg = u'Move this image to the Trash?\n\n%s' % delete_path
            if label_paths:
                msg += u'\n\nIts annotation file(s) will be moved too:\n' + '\n'.join(label_paths)
            if QMessageBox.warning(self, u'Delete image', msg,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            if not self.canvas.shapes:
                self.set_clean()
            idx = self.cur_img_idx
            for path in [delete_path] + label_paths:
                if os.path.exists(path) and not QFile.moveToTrash(path)[0]:
                    QMessageBox.critical(self, u'Delete image', u'Could not move to Trash:\n%s' % path)
                    return
            self.undo_history.pop(delete_path, None)
            self.import_dir_images(self.dir_name)
            if self.img_count > 0:
                self.cur_img_idx = min(idx, self.img_count - 1)
                filename = self.m_img_list[self.cur_img_idx]
                self.load_file(filename)
            else:
                self.close_file()

    def reset_all(self):
        self.settings.reset()
        self.close()
        process = QProcess()
        process.startDetached(os.path.abspath(__file__))

    def may_continue(self):
        if not self.dirty:
            return True
        else:
            discard_changes = self.discard_changes_dialog()
            if discard_changes == QMessageBox.No:
                return True
            elif discard_changes == QMessageBox.Yes:
                self.save_file()
                return True
            else:
                return False

    def discard_changes_dialog(self):
        yes, no, cancel = QMessageBox.Yes, QMessageBox.No, QMessageBox.Cancel
        msg = u'You have unsaved changes, would you like to save them and proceed?\nClick "No" to undo all changes.'
        return QMessageBox.warning(self, u'Attention', msg, yes | no | cancel)

    def error_message(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))

    def current_path(self):
        return os.path.dirname(self.file_path) if self.file_path else '.'

    def choose_color1(self):
        color = self.color_dialog.getColor(self.line_color, u'Choose line color',
                                           default=DEFAULT_LINE_COLOR)
        if color:
            self.line_color = color
            Shape.line_color = color
            self.canvas.set_drawing_color(color)
            self.canvas.update()
            self.set_dirty()

    def delete_selected_shape(self):
        if self.canvas.selected_shape is None:
            return
        self.record_edit()
        self.remove_label(self.canvas.delete_selected())
        self.set_dirty()
        if self.no_shapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    def choose_shape_line_color(self):
        color = self.color_dialog.getColor(self.line_color, u'Choose Line Color',
                                           default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selected_shape.line_color = color
            self.canvas.update()
            self.set_dirty()

    def choose_shape_fill_color(self):
        color = self.color_dialog.getColor(self.fill_color, u'Choose Fill Color',
                                           default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selected_shape.fill_color = color
            self.canvas.update()
            self.set_dirty()

    def copy_shape(self):
        if self.canvas.selected_shape is None:
            # True if one accidentally touches the left mouse button before releasing
            return
        self.record_edit()
        self.canvas.end_move(copy=True)
        self.add_label(self.canvas.selected_shape)
        self.set_dirty()

    def move_shape(self):
        self.record_edit()
        self.canvas.end_move(copy=False)
        self.set_dirty()

    def load_pascal_xml_by_filename(self, xml_path):
        if self.file_path is None:
            return
        if os.path.isfile(xml_path) is False:
            return

        self.set_format(FORMAT_PASCALVOC)

        t_voc_parse_reader = PascalVocReader(xml_path)
        shapes = t_voc_parse_reader.get_shapes()
        self.load_labels(shapes)
        self.canvas.verified = t_voc_parse_reader.verified

    def load_yolo_txt_by_filename(self, txt_path):
        if self.file_path is None:
            return
        if os.path.isfile(txt_path) is False:
            return

        self.set_format(FORMAT_YOLO)
        if self._current_classes is not None:
            # The open dataset's class list (from its classes.txt or the user's choice); [] = unknown
            t_yolo_parse_reader = YoloReader(txt_path, self.image, self.classes_file_path,
                                             classes=self.label_hist if self.classes_known else [])
        else:
            # Single file opened without a dataset: classes.txt next to the label file
            t_yolo_parse_reader = YoloReader(txt_path, self.image)
        shapes = t_yolo_parse_reader.get_shapes()
        self.load_labels(shapes)
        self.canvas.verified = t_yolo_parse_reader.verified

    def load_create_ml_json_by_filename(self, json_path, file_path):
        if self.file_path is None:
            return
        if os.path.isfile(json_path) is False:
            return

        self.set_format(FORMAT_CREATEML)

        create_ml_parse_reader = CreateMLReader(json_path, file_path)
        shapes = create_ml_parse_reader.get_shapes()
        self.load_labels(shapes)
        self.canvas.verified = create_ml_parse_reader.verified

    def copy_previous_bounding_boxes(self):
        current_index = self.m_img_list.index(self.file_path)
        if current_index - 1 >= 0:
            prev_file_path = self.m_img_list[current_index - 1]
            self.record_edit()
            self.show_bounding_box_from_annotation_file(prev_file_path)
            self.save_file()

    def toggle_paint_labels_option(self):
        checked = self.display_label_option.isChecked()
        for shape in self.canvas.shapes:
            shape.paint_label = checked
        self.show_label_text_checkbox.blockSignals(True)
        self.show_label_text_checkbox.setChecked(checked)
        self.show_label_text_checkbox.blockSignals(False)
        self.canvas.update()

    def set_paint_labels(self, checked):
        self.display_label_option.setChecked(checked)
        self.toggle_paint_labels_option()

    def toggle_show_boxes(self, checked):
        """Hide / show every box on the image without touching the per-box checkboxes."""
        self.canvas.show_shapes = checked
        if not checked:
            self.canvas.de_select_shape()
        self.canvas.update()

    def toggle_draw_square(self):
        self.canvas.set_drawing_shape_to_square(self.draw_squares_option.isChecked())

def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        reader = QImageReader(filename)
        reader.setAutoTransform(True)
        return reader.read()
    except:
        return default


def get_main_app(argv=None):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    if not argv:
        argv = []
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(new_icon("app"))
    # Classes come from each dataset's classes.txt (asked for on Open Dir when missing)
    argparser = argparse.ArgumentParser()
    argparser.add_argument("image_dir", nargs="?")
    argparser.add_argument("save_dir", nargs="?")
    args = argparser.parse_args(argv[1:])

    args.image_dir = args.image_dir and os.path.normpath(args.image_dir)
    args.save_dir = args.save_dir and os.path.normpath(args.save_dir)

    # Usage : labelImg.py [image_or_dir] [save_dir]
    win = MainWindow(args.image_dir,
                     args.save_dir)
    # Keep single-key shortcuts working under Chinese (Zhuyin / Pinyin ...) input methods
    # and holding a / d keep stepping through images at a steady pace
    win.physical_key_shortcuts = PhysicalKeyShortcuts(win, win.hold_repeat_actions)
    app.installEventFilter(win.physical_key_shortcuts)
    win.show()
    return app, win


def main():
    """construct main app and run it"""
    app, _win = get_main_app(sys.argv)
    return app.exec_()

if __name__ == '__main__':
    sys.exit(main())
