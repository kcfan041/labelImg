#!/usr/bin/env python
# -*- coding: utf8 -*-
"""A QMenu that stays open while items are clicked, for picking several checkable items in a row.
It closes like any popup when clicking outside it, pressing Esc or clicking its button again."""
try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMenu
except ImportError:
    from PyQt4.QtCore import Qt
    from PyQt4.QtGui import QMenu


class StayOpenMenu(QMenu):

    def _keep_open(self, action):
        # Sub-menus and separators behave as usual
        return action is not None and action.isEnabled() and not action.isSeparator() and action.menu() is None

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.pos())
        if self._keep_open(action):
            action.trigger()  # toggles a checkable action; QMenu would also close here
            return
        super(StayOpenMenu, self).mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        action = self.activeAction()
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._keep_open(action):
            action.trigger()
            return
        super(StayOpenMenu, self).keyPressEvent(event)
