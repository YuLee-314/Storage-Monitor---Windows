# Storage Monitor - Windows 存储监测系统

实时监测 Windows 各磁盘分区的空间使用情况，提供可视化仪表盘。

## 架构

```
Rust (数据采集)          Python (GUI 展示)
─────────────          ─────────────────
windows-rs API  ──►   PySide6 仪表盘
     │                      │
     ▼                      ▼
GetDiskFreeSpaceExW    QProgressBar 进度条
GetVolumeInformationW  实时刷新 (5s)
GetLogicalDrives       系统托盘常驻
```

- **`rust-collector/`** — Rust crate，通过 `windows-rs` 调用 Win32 API，经 `pyo3` 暴露为 Python 模块
- **`gui/`** — PySide6 桌面应用，仪表盘窗口 + 系统托盘，5 秒自动刷新

## 快速开始

### 环境要求

- Windows 11
- Python 3.10+
- Rust 1.70+

### 安装与运行

```bash
# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install maturin PySide6

# 构建 Rust 扩展
maturin develop --manifest-path rust-collector/Cargo.toml --release

# 启动应用
python -m gui.main
```

### 打包为 .exe

```bash
python build.py
```

输出位于 `dist/StorageMonitor/` 目录。

## 功能

- 实时显示各磁盘分区使用率（进度条 + 数字）
- 颜色标识：绿色 (<50%) → 黄色 (50-75%) → 橙色 (75-90%) → 红色 (>90%)
- 显示卷标、文件系统、驱动器类型
- 系统托盘常驻，关闭窗口最小化到托盘
- 手动刷新按钮 + 5 秒自动刷新

## 项目结构

```
project/
├── rust-collector/           # Rust 数据采集层
│   ├── Cargo.toml
│   └── src/lib.rs            # pyo3 绑定 + Win32 API
├── gui/                      # Python GUI 层
│   ├── __init__.py
│   ├── main.py               # 入口
│   ├── dashboard.py           # 仪表盘窗口
│   └── requirements.txt
├── pyproject.toml            # Python 项目配置
├── build.py                  # PyInstaller 打包脚本
└── README.md
```
