# 工具类模块

from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtWidgets import QLayout, QTableWidgetItem


class FlowLayout(QLayout):
    """流式布局，自动换行"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSpacing(5)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def count(self):
        return len(self._items)

    def sizeHint(self):
        x = 0
        y = 0
        line_height = 30
        spacing = 5
        max_width = 0

        for item in self._items:
            wid = item.widget()
            if wid is None:
                continue
            size_hint = wid.sizeHint()
            if x + size_hint.width() > 800:
                x = 0
                y += line_height + spacing
            x += size_hint.width() + spacing
            max_width = max(max_width, x)

        return QSize(max_width, y + line_height)

    def minimumSize(self):
        return QSize(400, 50)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect)

    def doLayout(self, rect):
        x = rect.x()
        y = rect.y()
        line_height = 30
        spacing = 5

        for item in self._items:
            wid = item.widget()
            if wid is None:
                continue
            size_hint = wid.sizeHint()
            if x + size_hint.width() > rect.right():
                x = rect.x()
                y += line_height + spacing
            item.setGeometry(QRect(x, y, size_hint.width(), size_hint.height()))
            x += size_hint.width() + spacing

        self.parentWidget().setMinimumHeight(y + line_height + 10)

    def clear(self):
        for item in self._items:
            if item.widget():
                item.widget().deleteLater()
        self._items.clear()


class NumericTableItem(QTableWidgetItem):
    """支持数值排序的表格项"""
    def __lt__(self, other):
        if self.data(Qt.UserRole) is not None and other.data(Qt.UserRole) is not None:
            return self.data(Qt.UserRole) < other.data(Qt.UserRole)
        return super().__lt__(other)
