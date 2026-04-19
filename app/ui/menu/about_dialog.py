import base64
import hashlib

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

from app.config import about


class Decrypt:
    @staticmethod
    def decrypt(text: str) -> str:
        raw = hashlib.sha256(text.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class AboutDialog(QDialog):
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("关于留痕")
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        label = QLabel(about, self)
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        layout.addWidget(label)

        button = QPushButton("确定", self)
        button.clicked.connect(self.accept)
        layout.addWidget(button)
