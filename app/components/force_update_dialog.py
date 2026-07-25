"""强制更新对话框：版本过低时禁止使用软件，10秒倒计时后强制退出。

特性：
- 不可关闭（无 X 按钮、禁止 ESC、禁止遮罩点击）
- 10 秒倒计时，倒计时结束后强制退出软件
- 「前往下载」按钮打开浏览器
- 带记忆功能：下次启动即使检测不到新版本仍禁止使用
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from qfluentwidgets import MessageBox


def show_force_update_dialog(parent, remote_version: str, current_version: str,
                              download_url: str = "", notes: str = ""):
    """显示强制更新对话框（模态、不可关闭）。

    - remote_version: 远程最新版本号
    - current_version: 当前版本号
    - download_url: 下载地址
    - notes: 更新日志
    """
    try:
        from app.services import log_service
        log_service.log_ui_action("强制更新", f"远程={remote_version} 当前={current_version}")
    except Exception:
        pass

    countdown = 10

    msg = f"检测到新版本 {remote_version}，当前版本 {current_version} 过低。\n"
    msg += "为保障软件正常运行，请立即更新。\n"
    msg += f"\n软件将在 {countdown} 秒后自动退出。"
    if notes:
        msg += f"\n\n更新内容：\n{notes}"

    box = MessageBox("强制更新", msg, parent)
    try:
        # 不可关闭：隐藏取消按钮、禁止遮罩点击、移除关闭按钮
        box.cancelButton.hide()
        box.setClosableOnMaskClicked(False)
        box.setWindowFlag(Qt.WindowCloseButtonHint, False)
    except Exception:
        pass

    # 记录强制更新标记（记忆功能）
    try:
        from app.services.update_checker import mark_force_update_required
        mark_force_update_required(remote_version)
    except Exception:
        pass

    # 倒计时定时器
    state = {"count": countdown, "box": box}

    def _on_tick():
        state["count"] -= 1
        if state["count"] <= 0:
            try:
                from app.services import log_service
                log_service.log_ui_action("强制更新", "倒计时结束，强制退出软件")
            except Exception:
                pass
            try:
                box.close()
            except Exception:
                pass
            # 强制退出
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        # 更新对话框文字
        try:
            new_msg = f"检测到新版本 {remote_version}，当前版本 {current_version} 过低。\n"
            new_msg += "为保障软件正常运行，请立即更新。\n"
            new_msg += f"\n软件将在 {state['count']} 秒后自动退出。"
            if notes:
                new_msg += f"\n\n更新内容：\n{notes}"
            # MessageBox 内部有 contentLabel
            label = box.contentLabel if hasattr(box, 'contentLabel') else None
            if label is not None:
                label.setText(new_msg)
        except Exception:
            pass

    timer = QTimer(box)
    timer.timeout.connect(_on_tick)
    timer.start(1000)

    # 「前往下载」按钮：打开浏览器
    try:
        url = download_url or "https://github.com/ShumaMeme/MemeKit/releases"
        box.yesButton.setText("前往下载")
        box.yesButton.clicked.disconnect()
        def _on_download():
            try:
                import webbrowser
                webbrowser.open(url)
                try:
                    from app.services import log_service
                    log_service.log_ui_action("强制更新", "用户点击前往下载")
                except Exception:
                    pass
            except Exception:
                pass
        box.yesButton.clicked.connect(_on_download)
    except Exception:
        pass

    # 模态执行
    box.exec()


def show_force_update_from_memory(parent):
    """从记忆标记中恢复强制更新（启动时调用）。

    如果之前检测到需要强制更新，即使本次启动网络故障检测不到新版本，
    仍然禁止用户使用软件。
    """
    try:
        from app.services.update_checker import (
            is_force_update_required, get_force_update_version,
        )
        from app.version import VERSION

        if not is_force_update_required():
            return False

        remote_version = get_force_update_version() or "未知版本"
        show_force_update_dialog(parent, remote_version, VERSION)
        return True
    except Exception:
        return False
