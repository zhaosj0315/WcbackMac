import json
import os.path
import re
import sys
import traceback
from pathlib import Path
from urllib.parse import urljoin

import requests
from PyQt5.QtCore import pyqtSignal, QThread, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QWidget, QMessageBox, QFileDialog, QLineEdit

from app.DataBase import msg_db, misc_db, close_db
from app.DataBase.merge import merge_databases, merge_MediaMSG_databases
from app.components.QCursorGif import QCursorGif
from app.config import INFO_FILE_PATH, DB_DIR, SERVER_API_URL
from app.decrypt import get_wx_info, decrypt
from app.decrypt.decrypt import verify_db_key
from app.decrypt.macos_provider import build_probe, candidate_roots, find_databases
from app.log import logger
from app.util.os_support import IS_WINDOWS, IS_MACOS
from app.util import path
from . import decryptUi
from ...Icon import Icon
from ...menu.about_dialog import Decrypt


class DecryptControl(QWidget, decryptUi.Ui_Dialog, QCursorGif):
    DecryptSignal = pyqtSignal(bool)
    get_wxidSignal = pyqtSignal(str)
    versionErrorSignal = pyqtSignal(str)

    def __init__(self, parent=None):
        super(DecryptControl, self).__init__(parent)
        self.max_val = 0
        self.setupUi(self)
        # 设置忙碌光标图片数组
        self.initCursor([':/icons/icons/Cursors/%d.png' %
                         i for i in range(8)], self)
        self.setCursorTimeout(100)
        self.version_list = None
        self.btn_start.clicked.connect(self.decrypt)
        self.btn_getinfo.clicked.connect(self.get_info)
        self.btn_db_dir.clicked.connect(self.select_db_dir)
        # self.lineEdit.returnPressed.connect(self.set_wxid)
        # self.lineEdit.textChanged.connect(self.set_wxid_)
        self.lineEdit_name.returnPressed.connect(self.set_wxid)
        self.lineEdit_name.textChanged.connect(self.set_wxid_)
        self.lineEdit_phone.returnPressed.connect(self.set_wxid)
        self.lineEdit_phone.textChanged.connect(self.set_wxid_)
        self.btn_help.clicked.connect(self.show_help)
        self.btn_getinfo.setIcon(Icon.Get_info_Icon)
        self.btn_db_dir.setIcon(Icon.Folder_Icon)
        self.btn_start.setIcon(Icon.Start_Icon)
        self.btn_help.setIcon(Icon.Help_Icon)
        self.info = {}
        self.lineEdit_name.setFocus()
        self.ready = False
        self.wx_dir = None
        self.lineEdit_key = None
        if IS_MACOS:
            self.lineEdit_key = QLineEdit(self)
            self.lineEdit_key.setPlaceholderText("粘贴 64 位 hex 数据库密钥")
            self.label_key.hide()
            self.gridLayout.addWidget(self.lineEdit_key, 5, 1, 1, 1)
            self.label_9.setText("Mac 版本可自动定位数据库；密钥需输入，自动内存取 key 受 macOS 权限限制")

    def show_help(self):
        # 定义网页链接
        url = QUrl("https://blog.lc044.love/post/4")
        # 使用QDesktopServices打开网页
        QDesktopServices.openUrl(url)

    # @log
    def get_info(self):
        if not IS_WINDOWS:
            self.get_mac_info()
            return
        self.startBusy()
        self.get_info_thread = MyThread(self.version_list)
        self.get_info_thread.signal.connect(self.set_info)
        self.get_info_thread.start()

    def get_mac_info(self):
        self.startBusy()
        try:
            probe = build_probe(include_keychain=False, check_memory=True)
            processes = probe.get("processes", [])
            databases = probe.get("databases", [])
            encrypted = [db for db in databases if db.get("encrypted")]
            if not encrypted:
                QMessageBox.critical(self, "错误", "未找到 Mac 微信加密数据库")
                return

            wx_dir = self._infer_mac_wx_dir(encrypted[0]["path"])
            wxid = self._infer_mac_wxid(wx_dir or encrypted[0]["path"])
            self.wx_dir = wx_dir or str(Path(encrypted[0]["path"]).parent)
            self.info = {
                "pid": processes[0]["pid"] if processes else "None",
                "version": "Mac WeChat",
                "wxid": wxid,
                "name": wxid,
                "mobile": "",
                "key": "None",
            }
            self.label_pid.setText(str(self.info["pid"]))
            self.label_version.setText(self.info["version"])
            self.label_wxid.setText(wxid)
            self.lineEdit_name.setText(wxid)
            self.lineEdit_phone.setText("")
            self.label_db_dir.setText(self.wx_dir)
            if self.lineEdit_key:
                self.lineEdit_key.setText("")
            self.checkBox.setCheckable(True)
            self.checkBox.setChecked(True)
            self.checkBox_2.setCheckable(True)
            self.checkBox_2.setChecked(True)
            self.ready = True
            self.label_ready.setText("已定位数据库，请输入密钥")
            if probe.get("memory_permission", {}).get("allowed") is False:
                QMessageBox.information(
                    self,
                    "Mac 自动取 key 状态",
                    "已找到 Mac 微信加密数据库，但当前权限无法读取微信进程内存。\n"
                    "本版本先启用半自动解密：输入密钥后可自动验证并批量解密。",
                )
        except Exception:
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "错误", "Mac 微信探测失败，请查看日志")
        finally:
            self.stopBusy()

    @staticmethod
    def _infer_mac_wxid(path_text):
        match = re.search(r"(wxid_[^/]+?)(?:_\d+)?(?:/|$)", path_text)
        return match.group(1) if match else "wxid_mac"

    @staticmethod
    def _infer_mac_wx_dir(path_text):
        path_obj = Path(path_text)
        for parent in [path_obj, *path_obj.parents]:
            if parent.name.startswith("wxid_"):
                return str(parent)
        return None

    def set_info(self, result):
        # print(result)
        if result[0] == -1:
            QMessageBox.critical(self, "错误", "请登录微信")
        elif result[0] == -2:
            self.versionErrorSignal.emit(result[1])
            QMessageBox.critical(self, "错误",
                                 "微信版本不匹配\n请手动填写信息")

        elif result[0] == -3:
            QMessageBox.critical(self, "错误", "WeChat WeChatWin.dll Not Found")
        elif result[0] == -4:
            QMessageBox.critical(self, "错误", "当前系统不支持自动提取微信密钥")
        elif result[0] == -10086:
            QMessageBox.critical(self, "错误", "未知错误，请收集错误信息")
        else:
            self.ready = True
            self.info = result[0]
            self.label_key.setText(self.info['key'])
            self.label_wxid.setText(self.info['wxid'])
            self.lineEdit_name.setText(self.info['name'])
            self.lineEdit_phone.setText(self.info['mobile'])
            self.label_pid.setText(str(self.info['pid']))
            self.label_version.setText(self.info['version'])
            self.lineEdit_name.setFocus()
            self.checkBox.setCheckable(True)
            self.checkBox.setChecked(True)
            self.get_wxidSignal.emit(self.info['wxid'])
            directory = os.path.join(path.wx_path(), self.info['wxid'])
            if os.path.exists(directory):
                self.label_db_dir.setText(directory)
                self.wx_dir = directory
                self.checkBox_2.setCheckable(True)
                self.checkBox_2.setChecked(True)
                self.ready = True
            if self.ready:
                self.label_ready.setText('已就绪')
            if self.wx_dir and os.path.exists(os.path.join(self.wx_dir)):
                self.label_ready.setText('已就绪')
        self.stopBusy()

    def set_wxid_(self):
        if self.sender() == self.lineEdit_name:
            self.info['name'] = self.lineEdit_name.text()
        elif self.sender() == self.lineEdit_phone:
            self.info['mobel'] = self.lineEdit_phone.text()

    def set_wxid(self):
        if self.sender() == self.lineEdit_name:
            self.info['name'] = self.lineEdit_name.text()
            QMessageBox.information(self, "ok", f"昵称修改成功{self.info['name']}")
        elif self.sender() == self.lineEdit_phone:
            self.info['mobile'] = self.lineEdit_phone.text()
            QMessageBox.information(self, "ok", f"手机号修改成功{self.info['mobile']}")

    def select_db_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选取微信文件保存目录——能看到Msg文件夹",
            path.wx_path()
        )  # 起始路径
        if IS_MACOS:
            if not directory:
                return
            self.label_db_dir.setText(directory)
            self.wx_dir = directory
            self.checkBox_2.setCheckable(True)
            self.checkBox_2.setChecked(True)
            self.ready = True
            self.label_ready.setText('已就绪，请输入密钥')
            return
        db_dir = os.path.join(directory, 'Msg')
        if not os.path.exists(db_dir):
            QMessageBox.critical(self, "错误", "文件夹选择错误\n一般以wxid_xxx结尾")
            return

        self.label_db_dir.setText(directory)
        self.wx_dir = directory
        self.checkBox_2.setCheckable(True)
        self.checkBox_2.setChecked(True)
        if self.ready:
            self.label_ready.setText('已就绪')

    def decrypt(self):
        if IS_MACOS:
            self.decrypt_mac()
            return
        if not self.ready:
            QMessageBox.critical(self, "错误", "请先获取信息")
            return
        if not self.wx_dir:
            QMessageBox.critical(self, "错误", "请先选择微信安装路径")
            return
        if self.label_wxid.text() == 'None':
            QMessageBox.critical(self, "错误", "请填入wxid")
            return
        db_dir = os.path.join(self.wx_dir, 'Msg')
        if self.ready:
            if not os.path.exists(db_dir):
                QMessageBox.critical(self, "错误", "文件夹选择错误\n一般以wxid_xxx结尾")
                return
        if self.info.get('key') == 'None':
            QMessageBox.critical(self, "错误",
                                 "密钥错误\n请查看教程解决相关问题")
        close_db()
        self.thread2 = DecryptThread(db_dir, self.info['key'])
        self.thread2.maxNumSignal.connect(self.setProgressBarMaxNum)
        self.thread2.signal.connect(self.progressBar_view)
        self.thread2.okSignal.connect(self.btnExitClicked)
        self.thread2.errorSignal.connect(
            lambda x: QMessageBox.critical(self, "错误",
                                           "错误\n请检查微信版本是否为最新和微信路径是否正确\n或者关闭微信多开")
        )
        self.thread2.start()

    def decrypt_mac(self):
        if not self.ready:
            QMessageBox.critical(self, "错误", "请先获取信息")
            return
        if not self.wx_dir:
            QMessageBox.critical(self, "错误", "请先选择微信数据库目录")
            return
        key = self.lineEdit_key.text().strip() if self.lineEdit_key else self.label_key.text().strip()
        if len(key) != 64:
            QMessageBox.critical(self, "错误", "请输入 64 位 hex 数据库密钥")
            return
        close_db()
        self.thread2 = MacDecryptThread(self.wx_dir, key)
        self.thread2.maxNumSignal.connect(self.setProgressBarMaxNum)
        self.thread2.signal.connect(self.progressBar_view)
        self.thread2.okSignal.connect(lambda x: QMessageBox.about(self, "解密完成", x))
        self.thread2.errorSignal.connect(lambda x: QMessageBox.critical(self, "错误", x))
        self.thread2.start()

    def btnEnterClicked(self):
        # print("enter clicked")
        # 中间可以添加处理逻辑
        # QMessageBox.about(self, "解密成功", "数据库文件存储在app/DataBase/Msg文件夹下")
        self.progressBar_view(self.max_val)
        self.DecryptSignal.emit(True)
        # self.close()

    def setProgressBarMaxNum(self, max_val):
        self.max_val = max_val
        self.progressBar.setRange(0, max_val)

    def progressBar_view(self, value):
        """
        进度条显示
        :param value: 进度0-100
        :return: None
        """
        self.progressBar.setProperty('value', value)
        #     self.btnExitClicked()
        #     data.init_database()

    def btnExitClicked(self):
        # print("Exit clicked")
        dic = {
            'wxid': self.info['wxid'],
            'wx_dir': self.wx_dir,
            'name': self.info['name'],
            'mobile': self.info['mobile'],
            'token': Decrypt.decrypt(self.info['wxid'])
        }
        try:
            with open(INFO_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(dic, f, ensure_ascii=False, indent=4)
        except:
            with open('./info.json', 'w', encoding='utf-8') as f:
                f.write(json.dumps(dic))
        self.progressBar_view(self.max_val)
        self.DecryptSignal.emit(True)
        self.close()


class DecryptThread(QThread):
    signal = pyqtSignal(str)
    maxNumSignal = pyqtSignal(int)
    okSignal = pyqtSignal(str)
    errorSignal = pyqtSignal(bool)

    def __init__(self, db_path, key):
        super(DecryptThread, self).__init__()
        self.db_path = db_path
        self.key = key
        self.textBrowser = None

    def __del__(self):
        pass

    def run(self):
        close_db()
        output_dir = DB_DIR
        os.makedirs(output_dir, exist_ok=True)
        tasks = []
        if os.path.exists(self.db_path):
            for root, dirs, files in os.walk(self.db_path):
                for file in files:
                    if '.db' == file[-3:]:
                        if 'xInfo.db' == file:
                            continue
                        inpath = os.path.join(root, file)
                        # print(inpath)
                        output_path = os.path.join(output_dir, file)
                        tasks.append([self.key, inpath, output_path])
                    else:
                        try:
                            name, suffix = file.split('.')
                            if suffix.startswith('db_SQLITE'):
                                inpath = os.path.join(root, file)
                                # print(inpath)
                                output_path = os.path.join(output_dir, name + '.db')
                                tasks.append([self.key, inpath, output_path])
                        except:
                            continue
        self.maxNumSignal.emit(len(tasks))
        for i, task in enumerate(tasks):
            if decrypt.decrypt(*task) == -1:
                self.errorSignal.emit(True)
            self.signal.emit(str(i))
        # print(self.db_path)
        # 目标数据库文件
        target_database = os.path.join(DB_DIR, 'MSG.db')
        # 源数据库文件列表
        source_databases = [os.path.join(DB_DIR, f"MSG{i}.db") for i in range(1, 50)]
        import shutil
        if os.path.exists(target_database):
            os.remove(target_database)
        shutil.copy2(os.path.join(DB_DIR, 'MSG0.db'), target_database)  # 使用一个数据库文件作为模板
        # 合并数据库
        merge_databases(source_databases, target_database)

        # 音频数据库文件
        target_database = os.path.join(DB_DIR, 'MediaMSG.db')
        # 源数据库文件列表
        if os.path.exists(target_database):
            os.remove(target_database)
        source_databases = [os.path.join(DB_DIR, f"MediaMSG{i}.db") for i in range(1, 50)]
        shutil.copy2(os.path.join(DB_DIR, 'MediaMSG0.db'), target_database)  # 使用一个数据库文件作为模板

        # 合并数据库
        merge_MediaMSG_databases(source_databases, target_database)
        self.okSignal.emit('ok')
        # self.signal.emit('100')


class MacDecryptThread(QThread):
    signal = pyqtSignal(str)
    maxNumSignal = pyqtSignal(int)
    okSignal = pyqtSignal(str)
    errorSignal = pyqtSignal(str)

    def __init__(self, db_path, key):
        super().__init__()
        self.db_path = db_path
        self.key = key

    def run(self):
        try:
            roots = [Path(self.db_path)] if self.db_path else candidate_roots()
            databases = find_databases(roots)
            encrypted = [db for db in databases if db.encrypted]
            if not encrypted:
                self.errorSignal.emit("未找到可解密的 Mac 微信数据库")
                return
            verify_target = next((db for db in encrypted if db.name in {"message_0.db", "contact.db"}), encrypted[0])
            if not verify_db_key(self.key, verify_target.path):
                self.errorSignal.emit(f"密钥校验失败：{verify_target.path}")
                return

            output_dir = Path("./app/Database/MacMsg")
            output_dir.mkdir(parents=True, exist_ok=True)
            self.maxNumSignal.emit(len(encrypted))
            success = 0
            failed = 0
            source_root = roots[0]
            for i, db in enumerate(encrypted):
                src = Path(db.path)
                try:
                    rel = src.relative_to(source_root)
                except ValueError:
                    rel = Path(src.name)
                dest = output_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                ok, ret = decrypt.decrypt(self.key, str(src), str(dest))
                if ok:
                    success += 1
                else:
                    failed += 1
                    logger.error(ret)
                self.signal.emit(str(i + 1))
            self.okSignal.emit(f"Mac 数据库解密完成\n成功：{success}\n失败：{failed}\n输出：{output_dir.resolve()}")
        except Exception:
            logger.error(traceback.format_exc())
            self.errorSignal.emit("Mac 数据库解密失败，请查看日志")


class MyThread(QThread):
    signal = pyqtSignal(list)

    def __init__(self, version_list=None):
        super(MyThread, self).__init__()
        self.version_list = version_list

    def __del__(self):
        pass

    def get_bias_add(self, version):
        url = urljoin(SERVER_API_URL, 'wxBiasAddr')
        data = {
            'version': version
        }
        try:
            response = requests.get(url, json=data)
            print(response)
            print(response.text)
            if response.status_code == 200:
                update_info = response.json()
                return update_info
            else:
                return {}
        except:
            return {}

    def run(self):
        if self.version_list:
            VERSION_LIST = self.version_list
        else:
            file_path = './app/resources/data/version_list.json'
            if not os.path.exists(file_path):
                resource_dir = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(__file__)))
                file_path = os.path.join(resource_dir, 'app', 'resources', 'data', 'version_list.json')
            with open(file_path, "r", encoding="utf-8") as f:
                VERSION_LIST = json.loads(f.read())
        try:
            result = get_wx_info.get_info(VERSION_LIST)
            if result == -1:
                result = [result]
            elif result == -2:
                result = [result]
            elif result == -3:
                result = [result]
            elif isinstance(result, str):
                version = result
                # version = '3.9.9.43'
                version_bias = self.get_bias_add(version)
                if version_bias.get(version):
                    logger.info(f"从云端获取内存基址:{version_bias}")
                    result = get_wx_info.get_info(version_bias)
                else:
                    logger.info(f"从云端获取内存基址失败:{version}")
                    result = [-2, version]
        except:
            logger.error(traceback.format_exc())
            result = [-10086]
        self.signal.emit(result)
