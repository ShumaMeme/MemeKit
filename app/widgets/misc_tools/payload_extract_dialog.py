import os

from PySide6.QtCore import QObject, Signal, QThread, Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLabel,
    QFileDialog,
)
from app.components.log_widget import LogWidget
from app.components.blur_popup import show_blur_info, show_blur_dialog

from qfluentwidgets import (
    CardWidget,
    TitleLabel,
    CaptionLabel,
    BodyLabel,
    LineEdit,
    PushButton,
    PrimaryPushButton,
    CheckBox,
    isDarkTheme,
)

from app.logic.payload_extractor import PayloadExtractor


class _PayloadWorker(QThread):
    log = Signal(str)
    step_start = Signal(str, str)
    step_finish = Signal(str, bool, str)
    result_ready = Signal()
    error = Signal(str)

    def __init__(self, source: str, out_dir: str, partitions: list, parent=None):
        super().__init__(parent)
        self.source = source
        self.output_dir = out_dir
        self.partitions = partitions
        self._stop = False
        self._extractor = None

    def stop(self):
        self._stop = True
        try:
            if self._extractor is not None:
                self._extractor.stop()
        except Exception:
            pass

    def run(self):
        try:
            self._extractor = PayloadExtractor(
                log_callback=self.log.emit,
                step_start=self.step_start.emit,
                step_finish=self.step_finish.emit
            )
            ok = self._extractor.extract(self.source, self.output_dir, self.partitions)
            if ok:
                self.result_ready.emit()
            else:
                self.error.emit("提取失败")
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self._extractor = None


class _PayloadExtractDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("payload.bin 处理")
        self.resize(920, 640)
        self._worker = None

        layout = QVBoxLayout(self)
        try:
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)
        except Exception:
            pass

        header = CardWidget(self)
        header_lay = QVBoxLayout(header)
        header_lay.setContentsMargins(16, 14, 16, 14)
        header_lay.setSpacing(4)
        header_lay.addWidget(TitleLabel('Payload.bin 处理', header))
        header_lay.addWidget(CaptionLabel('支持本地 payload.bin/ZIP 提取', header))
        layout.addWidget(header)

        file_card = CardWidget(self)
        file_layout = QVBoxLayout(file_card)
        file_layout.setContentsMargins(16, 12, 16, 12)
        file_layout.setSpacing(8)
        file_layout.addWidget(BodyLabel('本地文件', file_card))

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.local_edit = LineEdit(file_card)
        self.local_edit.setPlaceholderText("选择 payload.bin 或包含 payload.bin 的 ZIP 文件")
        btn_browse = PushButton('浏览...', file_card)
        btn_browse.clicked.connect(self._browse_local)
        file_row.addWidget(self.local_edit, 1)
        file_row.addWidget(btn_browse)
        file_layout.addLayout(file_row)
        layout.addWidget(file_card)

        partition_group = CardWidget(self)
        partition_layout = QVBoxLayout(partition_group)
        partition_layout.setContentsMargins(16, 12, 16, 12)
        partition_layout.setSpacing(8)
        partition_layout.addWidget(BodyLabel('分区过滤（可选）', partition_group))
        self.partition_edit = LineEdit(partition_group)
        self.partition_edit.setPlaceholderText("例如: boot,vendor,system 或留空提取全部")
        partition_layout.addWidget(self.partition_edit)
        layout.addWidget(partition_group)

        out_group = CardWidget(self)
        out_layout = QVBoxLayout(out_group)
        out_layout.setContentsMargins(16, 12, 16, 12)
        out_layout.setSpacing(8)
        out_layout.addWidget(BodyLabel('输出目录', out_group))

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.out_edit = LineEdit(out_group)
        self.out_edit.setPlaceholderText("选择输出目录")
        btn_out = PushButton('浏览...', out_group)
        btn_out.clicked.connect(self._browse_output)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn_out)
        out_layout.addLayout(out_row)
        layout.addWidget(out_group)

        btn_layout = QHBoxLayout()
        self.run_btn = PrimaryPushButton('开始提取', self)
        self.run_btn.clicked.connect(self._run_extract)
        self.cancel_btn = PushButton('取消', self)
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setEnabled(False)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.log = LogWidget()
        layout.addWidget(self.log)

        self.refresh_theme()

    def refresh_theme(self):
        """主题切换时刷新内部组件样式。"""
        if isDarkTheme():
            self.setStyleSheet("")
        else:
            self.setStyleSheet("QDialog { background-color: #F0E6F6; }")
        try:
            if hasattr(self.log, "refresh_theme"):
                self.log.refresh_theme()
        except Exception:
            pass

    def _browse_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            "Payload 文件 (payload.bin *.zip);;所有文件 (*.*)"
        )
        if path:
            self.local_edit.setText(path)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.out_edit.setText(path)

    def _run_extract(self):
        source = self.local_edit.text().strip()
        if not source or not os.path.exists(source):
            show_blur_info(self, "提示", "请选择有效的文件")
            return

        out_dir = self.out_edit.text().strip()
        if not out_dir:
            show_blur_info(self, "提示", "请选择输出目录")
            return

        os.makedirs(out_dir, exist_ok=True)

        partitions = self.partition_edit.text().strip()

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log.clear_log()
        self.log.append_log(f"开始提取...")
        self.log.append_log(f"源: {source}")
        self.log.append_log(f"输出: {out_dir}")
        if partitions:
            self.log.append_log(f"分区: {partitions}")
        else:
            self.log.append_log("分区: 全部")
        self.log.append_log("")

        self._worker = _PayloadWorker(source, out_dir, partitions, parent=self)

        self._worker.log.connect(lambda msg: self.log.append_log(msg))
        self._worker.step_start.connect(self.log.start_step, Qt.QueuedConnection)
        self._worker.step_finish.connect(self.log.finish_step, Qt.QueuedConnection)
        self._worker.result_ready.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._worker.start()

    def _cancel(self):
        if self._worker:
            self._worker.stop()
        self.log.append_log("\n用户取消操作")
        self._cleanup()

    def _on_log(self, msg):
        self.log.append_log(msg)

    def _on_finished(self):
        self.log.append_log("\n✅ 提取完成！")
        self._cleanup()

    def _on_error(self, error):
        self.log.append_log(f"\n❌ 错误: {error}")
        self._cleanup()

    def _cleanup(self):
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(100)
        self._worker = None
        self._worker = None
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            result = show_blur_dialog(self, "确认", "提取正在进行中，确定要关闭吗？")
            if not result:
                event.ignore()
                return
            if self._worker:
                self._worker.stop()
        self._cleanup()
        super().closeEvent(event)
