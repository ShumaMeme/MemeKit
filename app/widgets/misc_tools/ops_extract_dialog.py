import os
from threading import Event

from PySide6.QtCore import Signal, QThread
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
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
)


class _OpsWorker(QThread):
    """OPS 固件解包后台线程

    使用开源 opscrypto 算法（SM4-like 自定义加密）解密一加 .ops 固件。
    算法来源: https://github.com/bkerler/oppo_decrypt (MIT License)
    """
    log = Signal(str)
    result_ready = Signal()
    error = Signal(str)

    def __init__(self, source: str, out_dir: str, parent=None):
        super().__init__(parent)
        self.source = source
        self.output_dir = out_dir
        self._cancel_event = Event()

    def stop(self):
        self._cancel_event.set()

    def run(self):
        try:
            from app.logic.ops_crypto import extract_ops

            def log_cb(msg):
                self.log.emit(msg)

            ok = extract_ops(
                self.source,
                self.output_dir,
                log_callback=log_cb,
                cancel_event=self._cancel_event,
            )

            if self._cancel_event.is_set():
                self.error.emit("用户取消操作")
            elif ok:
                self.result_ready.emit()
            else:
                self.error.emit("OPS 解包失败，请查看日志")
        except Exception as e:
            self.error.emit(str(e))


class _OpsExtractDialog(QDialog):
    """OPS 固件解包对话框，UI 布局与 Payload.bin 解包保持一致"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OPS 解包")
        self.resize(920, 640)
        self._worker = None
        # 无边框 + 透明背景 + 毛玻璃样式
        try:
            from app.components.dialog_styles import dialog_stylesheet, setup_dialog_window
            setup_dialog_window(self)
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            pass

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
        header_lay.addWidget(TitleLabel('OPS 解包', header))
        header_lay.addWidget(CaptionLabel('OnePlus OPS 加密固件解包（自动解密分区镜像）', header))
        layout.addWidget(header)

        file_card = CardWidget(self)
        file_layout = QVBoxLayout(file_card)
        file_layout.setContentsMargins(16, 12, 16, 12)
        file_layout.setSpacing(8)
        file_layout.addWidget(BodyLabel('本地文件', file_card))

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self.local_edit = LineEdit(file_card)
        self.local_edit.setPlaceholderText("选择 .ops 固件文件")
        btn_browse = PushButton('浏览...', file_card)
        btn_browse.clicked.connect(self._browse_local)
        file_row.addWidget(self.local_edit, 1)
        file_row.addWidget(btn_browse)
        file_layout.addLayout(file_row)
        layout.addWidget(file_card)

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
        self.run_btn = PrimaryPushButton('开始解包', self)
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
        try:
            from app.components.dialog_styles import dialog_stylesheet
            self.setStyleSheet(dialog_stylesheet())
        except Exception:
            # fallback: 透明背景，让模糊层透出
            self.setStyleSheet("QDialog { background: transparent; }")
        try:
            if hasattr(self.log, "refresh_theme"):
                self.log.refresh_theme()
        except Exception:
            pass

    def _browse_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "",
            "OPS 固件 (*.ops);;所有文件 (*.*)"
        )
        if path:
            self.local_edit.setText(path)
            try:
                from app.services import log_service
                log_service.log_file_event("选择", path)
            except Exception:
                pass

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.out_edit.setText(path)
            try:
                from app.services import log_service
                log_service.log_file_event("选择输出", path)
            except Exception:
                pass

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

        try:
            from app.services import log_service
            detail = f"源={os.path.basename(source)} 输出={out_dir}"
            log_service.log_ui_action("OPS解包", detail)
        except Exception:
            pass

        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log.clear_log()
        self.log.append_log("开始解包...")
        self.log.append_log("")

        self._worker = _OpsWorker(source, out_dir, parent=self)

        self._worker.log.connect(lambda msg: self.log.append_log(msg))
        self._worker.result_ready.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._worker.start()

    def _cancel(self):
        try:
            from app.services import log_service
            log_service.log_ui_action("OPS解包-取消")
        except Exception:
            pass
        if self._worker:
            self._worker.stop()
        self.log.append_log("\n用户取消操作")
        self._cleanup()

    def _on_finished(self):
        self.log.append_log("\n✅ 解包完成！")
        try:
            from app.services import log_service
            log_service.log_operation("OPS解包", success=True, detail="解包完成")
        except Exception:
            pass
        self._cleanup()

    def _on_error(self, error):
        self.log.append_log(f"\n❌ 错误: {error}")
        try:
            from app.services import log_service
            log_service.log_operation("OPS解包", success=False, detail=str(error))
        except Exception:
            pass
        self._cleanup()

    def _cleanup(self):
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(100)
        self._worker = None
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            result = show_blur_dialog(self, "确认", "解包正在进行中，确定要关闭吗？")
            if not result:
                event.ignore()
                return
            if self._worker:
                self._worker.stop()
        self._cleanup()
        super().closeEvent(event)
