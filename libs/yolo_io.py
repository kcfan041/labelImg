#!/usr/bin/env python
# -*- coding: utf8 -*-
import codecs
import os

from libs.constants import DEFAULT_ENCODING

TXT_EXT = '.txt'
ENCODE_METHOD = DEFAULT_ENCODING

class YOLOWriter:

    def __init__(self, folder_name, filename, img_size, database_src='Unknown', local_img_path=None):
        self.folder_name = folder_name
        self.filename = filename
        self.database_src = database_src
        self.img_size = img_size
        self.box_list = []
        self.local_img_path = local_img_path
        self.verified = False

    def add_bnd_box(self, x_min, y_min, x_max, y_max, name, difficult, confidence=None):
        bnd_box = {'xmin': x_min, 'ymin': y_min, 'xmax': x_max, 'ymax': y_max}
        bnd_box['name'] = name
        bnd_box['difficult'] = difficult
        bnd_box['confidence'] = confidence
        self.box_list.append(bnd_box)

    def bnd_box_to_yolo_line(self, box, class_list=[]):
        x_min = box['xmin']
        x_max = box['xmax']
        y_min = box['ymin']
        y_max = box['ymax']

        x_center = float((x_min + x_max)) / 2 / self.img_size[1]
        y_center = float((y_min + y_max)) / 2 / self.img_size[0]

        w = float((x_max - x_min)) / self.img_size[1]
        h = float((y_max - y_min)) / self.img_size[0]

        # PR387
        box_name = box['name']
        if box_name not in class_list:
            class_list.append(box_name)

        class_index = class_list.index(box_name)

        return class_index, x_center, y_center, w, h

    def save(self, class_list=[], target_file=None, classes_file=None):

        if target_file is None:
            target_file = self.filename + TXT_EXT
        if classes_file is None:
            classes_file = os.path.join(os.path.dirname(os.path.abspath(target_file)), "classes.txt")

        # Build all lines before opening (and truncating) either file,
        # so an error mid-way can't leave an empty classes.txt / label file behind
        box_lines = []
        for box in self.box_list:
            class_index, x_center, y_center, w, h = self.bnd_box_to_yolo_line(box, class_list)
            line = "%d %.6f %.6f %.6f %.6f" % (class_index, x_center, y_center, w, h)
            # Write the model's confidence (6th column) back exactly as it was read
            if box.get('confidence') is not None:
                line += " %s" % box['confidence']
            box_lines.append(line + "\n")

        with codecs.open(target_file, 'w', encoding=ENCODE_METHOD) as out_file:
            out_file.writelines(box_lines)
        with codecs.open(classes_file, 'w', encoding=ENCODE_METHOD) as out_class_file:
            for c in class_list:
                out_class_file.write(c + '\n')



class YoloReader:

    def __init__(self, file_path, image, class_list_path=None):
        # shapes type:
        # [labbel, [(x1,y1), (x2,y2), (x3,y3), (x4,y4)], color, color, difficult]
        self.shapes = []
        self.file_path = file_path

        if class_list_path is None:
            dir_path = os.path.dirname(os.path.realpath(self.file_path))
            self.class_list_path = os.path.join(dir_path, "classes.txt")
        else:
            self.class_list_path = class_list_path

        # print (file_path, self.class_list_path)

        # Without classes.txt the labels fall back to their numeric class ids (see yolo_line_to_shape)
        self.classes = []
        if os.path.isfile(self.class_list_path):
            with codecs.open(self.class_list_path, 'r', encoding=ENCODE_METHOD) as classes_file:
                self.classes = classes_file.read().strip('\n').split('\n')

        # print (self.classes)

        img_size = [image.height(), image.width(),
                    1 if image.isGrayscale() else 3]

        self.img_size = img_size

        self.verified = False
        # try:
        self.parse_yolo_format()
        # except:
        #     pass

    def get_shapes(self):
        return self.shapes

    def add_shape(self, label, x_min, y_min, x_max, y_max, difficult, confidence=None):

        points = [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]
        self.shapes.append((label, points, None, None, difficult, confidence))

    def yolo_line_to_shape(self, class_index, x_center, y_center, w, h):
        idx = int(class_index)
        if idx < len(self.classes):
            label = self.classes[idx]
        else:
            label = str(idx)

        x_min = max(float(x_center) - float(w) / 2, 0)
        x_max = min(float(x_center) + float(w) / 2, 1)
        y_min = max(float(y_center) - float(h) / 2, 0)
        y_max = min(float(y_center) + float(h) / 2, 1)

        x_min = round(self.img_size[1] * x_min)
        x_max = round(self.img_size[1] * x_max)
        y_min = round(self.img_size[0] * y_min)
        y_max = round(self.img_size[0] * y_max)

        return label, x_min, y_min, x_max, y_max

    def parse_yolo_format(self):
        bnd_box_file = open(self.file_path, 'r')
        for bndBox in bnd_box_file:
            fields = bndBox.split()
            if len(fields) < 5:
                continue  # skip blank / malformed lines
            # Model predictions may carry a 6th confidence column
            class_index, x_center, y_center, w, h = fields[:5]
            # Kept as the original text so saving writes it back unchanged (e.g. '0.87', not '0.870000')
            confidence = None
            if len(fields) >= 6:
                try:
                    float(fields[5])
                    confidence = fields[5]
                except ValueError:
                    confidence = None
            label, x_min, y_min, x_max, y_max = self.yolo_line_to_shape(class_index, x_center, y_center, w, h)

            # Caveat: difficult flag is discarded when saved as yolo format.
            self.add_shape(label, x_min, y_min, x_max, y_max, False, confidence)
