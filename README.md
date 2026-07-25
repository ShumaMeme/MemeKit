# MemeKit Android 玩机工具箱

> 基于 GitHub 项目 [Tobatools](https://github.com/Tobapuww/Tobatools)（拖把工具箱）二次开发衍生

[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.14-yellow.svg)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/UI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20MacOS-lightgrey.svg)](#)

## 项目简介

MemeKit 是一款 Android 通用玩机工具箱，依托 ADB / Fastboot 机制实现刷机、ROOT、设备调试、分区管理等全套功能。界面采用 Fluent Design 现代化设计，原生适配 **Windows** 与 **MacOS** 双平台。

- **Windows**：云母 / 毛玻璃特效、原生驱动安装
- **MacOS**：原生红绿灯标题栏、Apple HIG 规范边距、系统字体渲染

## 目录

- [核心优势](#核心优势)
- [功能一览](#功能一览)
- [操作教程](#操作教程)
- [风险须知](#风险须知)
- [开源许可](#开源许可)
- [致谢](#致谢)

---

## 核心优势

**全设备兼容**
- 品牌：小米、拯救者、摩托罗拉、联想、一加、三星、真我等
- 芯片：高通、联发科、展锐、三星 SoC
- 类型：手机、平板、电视盒子、智能手表
- 固件：官方原厂包、第三方 ROM、救砖固件

**安全防护**
- 自动识别 ADB / Fastboot / FastbootD / EDL / Rec 设备模式
- 模式权限隔离，杜绝误刷分区
- 自动识别 A/B 双分区机型，自动切换槽位
- 完整操作日志留存（含毫秒级时间戳与操作耗时），方便故障排查

**流畅体验**
- 深浅双主题，跟随系统自动切换
- 全异步多线程，操作不卡顿 UI
- Tab 懒加载，启动速度优化
- 启动动画、弹窗模糊美化
- 毫秒级性能埋点，启动与操作全程可观测

## 功能一览

| 模块 | 说明 |
|------|------|
| **仪表盘** | 读取设备序列号、硬件参数、电池状态、系统版本、锁机状态；一键切换启动模式（Recovery / Bootloader / FastbootD / EDL 9008）、打开 ADB 终端、重启 ADB 服务（Windows 可安装 Fastboot 通用驱动 / OPPO USB 驱动 / 高通 EDL 驱动） |
| **一键 ROOT** | 兼容 Magisk / KernelSU / APatch / Magisk Alpha / Sukisu Ultra；自动修补 boot、init_boot 镜像并刷入 |
| **Flash 菜单** | ADB Sideload 刷机、OPlus 线刷（OPPO/一加专用）、单分区自定义刷入、Payload.bin 固件提取、OPS 固件解包 |
| **快捷指令** | 内置常用 ADB / Fastboot / Shell 调试命令，支持自定义新增、分类管理，一键执行并实时输出日志 |
| **投屏中心** | 内置 Scrcpy，USB 低延迟投屏 + 屏幕录制，可直接操控手机，刷机调试同步进行 |
| **备份字库** | 通过 Root 权限 + dd 命令完整备份字库分区（自动识别骁龙/MTK 处理器对应分区路径），实时读写进度反馈，备份完成后自动生成隐藏设备信息文件，**完整记录备份耗时** |
| **还原字库** | 通过 Fastboot 批量刷写 .img 镜像文件恢复字库分区，自动识别 MemeKit 备份目录，**完整记录还原耗时** |
| **软件管理** | 批量安装 / 卸载（含保留数据）/ 导出 APK / 冻结 / 解冻 / 强制停止 / 清除数据 / 禁用 Activity / 查看权限 |
| **文件管理** | 设备与电脑双向批量传输文件，直接浏览系统分区，支持复制/剪切/重命名/删除/查看属性 |
| **设置面板** | 切换主题、检测依赖工具、检查更新、导出/清空运行日志、查看项目信息、关于作者、免责声明 |

## 操作教程

### 初次使用

1. 手机开启「开发者选项」→ 启用「USB 调试」
2. 数据线连接电脑，手机上授权设备调试
3. 软件自动识别设备，即可使用全部功能

### 基础刷机流程

1. 打开「Flash 菜单」，选择刷写模式（ADB Sideload / OPlus 线刷 / 单分区刷写）
2. 载入固件文件（支持 Payload.bin / OPS / 镜像文件）
3. 确认设备当前启动模式无误
4. 点击开始，等待刷写完成（日志区显示完整耗时）

### ROOT 操作流程

1. 进入「一键 ROOT」，选择 Root 管理器方案（Magisk / KernelSU / APatch / Magisk Alpha / Sukisu Ultra）
2. 工具自动推送 ROOT 管理器至手机
3. 根据弹窗提示完成修补与刷入

### 字库备份/还原流程

1. 进入「备份字库」，选择电脑保存目录，点击「开始备份」
2. 工具自动识别处理器类型（骁龙/MTK）并扫描分区表
3. 通过 dd 命令逐个备份分区到电脑，完成后弹窗显示**完整耗时**
4. 还原时进入「还原字库」，选择备份目录，工具自动识别 MemeKit 备份文件夹
5. 确认镜像列表后通过 Fastboot 批量刷入，完成后显示**完整耗时**

## 风险须知

> ⚠️ **重要提示**：请务必仔细阅读以下内容

1. 刷机、解锁 Bootloader 会清空手机全部数据，操作前务必备份资料
2. 仅可使用对应机型固件，错刷镜像会导致设备变砖
3. 部分厂商设备解锁 BL 会失去官方保修
4. 本工具仅用于个人学习研究，操作产生设备损坏由使用者自行承担

### 免责声明

本项目为免费开源工具，作者不承担任何因使用软件造成的硬件损坏、数据丢失相关责任。

## 开源许可

项目基于 **GPLv3** 开源协议，附加非商用限制：

**✅ 允许**
- 免费下载、使用、修改源码
- 分享工具、二次开发衍生版本
- 个人学习、非商用刷机、ROM 适配使用
- 提交代码 Bug 修复、功能 PR

**❌ 禁止**
- 用于任何付费刷机、商业维修服务
- 打包闭源商用软件、倒卖源码
- 基于本项目开发收费工具售卖

如需商用授权，请联系项目作者单独申请。

## 致谢

- UI 框架基于开源库 [QFluentWidgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) 开发
- 感谢上游 Tobatools 项目与全体开源社区开发者

---

**MemeKit - Android 玩机工具箱**
