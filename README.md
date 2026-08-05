# TestTool

跨平台网络测试工具。支持批量 TCP 连通性检测、TCP/WebSocket 协议测试、IP/端口范围展开、端口扫描、集合管理、CSV/Excel/JSON 导入导出。

![Windows](https://img.shields.io/badge/Windows-x64-blue)
![Linux](https://img.shields.io/badge/Linux-x64%20%7C%20ARM64%20%7C%20Compat-orange)
![macOS](https://img.shields.io/badge/macOS-ARM64-silver)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 功能特性

### 连通性测试
- **TCP 连通性检测** — 多线程并发（最高 200），实时进度，支持终止
- **超时精度** — 小数秒（0.1~60s），适配不同网络环境
- **目标列表** — 全选/反选/刷新，双击行直接测试该目标，测试完成自动刷新状态
- **右键菜单** — 添加/编辑/删除/协议测试/测试连通性，操作更快捷
- **IP 范围支持** — 单 IP、CIDR、范围、换行分隔，添加/编辑时合法性校验
- **端口范围支持** — 单端口、范围、逗号、换行、混合格式
- **端口扫描** — 扫描开放端口后导入集合
- **高并发稳定性** — 批量写库 + 信号线程锁 + QThread 安全清理

### 协议测试
- **TCP/WebSocket 客户端** — 连接参数自定义（编码/HeadLen/超时），发送编码在发送按钮旁，接收编码在响应区
- **预设报文模板** — 添加/删除（支持多选）/重命名/清空，保存按钮在发送区，右键菜单集成全部操作
- **响应显示** — 接收编码选择 + 自动检测 + 十六进制/文本切换
- **Mock 服务端 / 服务端** — TCP/WebSocket 监听，固定/回显响应模式，列表含发送/接收编码与运行状态列，双击行编辑
- **编码联动** — 运行中修改编码自动同步到服务列表与监听器
- **集合导入导出** — JSON 格式，含目标参数、预设报文、Mock 服务端完整配置
- **独立客户端** — 快速测试，参数自动持久化，可一键保存配置到测试集合
- **批量操作** — 服务端支持多选启动/停止/删除，列排序
- **测试历史** — 刷新/删除/清空/导出（支持多选导出），全局历史与集合历史一致

### 管理
- **集合管理** — 树形分类（未分类 + 自定义集合），搜索过滤，支持拖拽排序
- **集合操作** — 新建/编辑/删除/导入/导出，右键菜单集成，新建默认命名「连通性测试集合N/协议测试集合N」
- **筛选过滤** — 文本搜索 + 状态/类型筛选
- **列排序** — 点击列标题排序，箭头指示升降序
- **批量操作** — 集合/目标/服务端均支持多选批量操作

### 导入导出
- **CSV/Excel** — 含样式，IP/端口范围展开
- **JSON** — 协议集合完整配置导入导出
- **防重** — 集合 + IP + 端口判定重复
- **异步导入** — 大数量二次确认 + 进度条

### 存储
- **SQLite** — 本地单文件，WAL 模式

## 使用方法

### 源码运行

```bash
pip install PySide6 openpyxl xlrd Pillow
python main.py
```

### 命令行模式

```bash
python main.py --cli 192.168.1.1 22 10.0.0.1 80 --timeout 3
```

### 连通性测试

- **按集合测试**：左侧选集合 → 切换到「连通测试」→ 勾选目标 → 点击开始
- **快速测试**：目标列表中双击任意目标行即可测试该条连通性
- **终止测试**：测试中点击「⏹ 停止测试」
- **实时筛选**：结果表格支持文本搜索 + 状态筛选

### 协议测试

- **客户端**：切换到「协议测试」→「客户端」tab，配置参数后发送；响应区选择接收编码
- **预设报文**：发送区「保存」保存当前报文，预设列表右键可添加/删除/重命名/清空
- **Mock 服务端 / 服务端**：添加监听器（双击行或右键可编辑），启动后独立日志 tab，列表显示编码与运行状态
- **集合测试**：左侧集合列表双击目标打开详情页（切换集合不关闭已打开页），详情页 tab 以描述或 ip:port 命名
- **独立客户端**：配置完成后可点「保存到集合」把当前配置存为集合中的目标

## 项目结构

```
TestTool/
├── main.py                        # 入口（GUI + CLI）
├── requirements.txt
└── src/
    ├── database.py                # SQLite 数据层
    ├── scanner.py                 # TCP 并发检测引擎 + IP/端口展开
    ├── protocol.py                # TCP/WS 协议引擎（收发/服务端）
    ├── csv_handler.py             # CSV 导入导出
    ├── excel_handler.py           # Excel 导入导出
    ├── json_handler.py            # JSON 协议配置导入导出
    └── ui/
        ├── main_window.py         # 主窗口
        ├── connectivity_panel.py  # 连通测试面板（集合+目标+测试+历史）
        ├── protocol_panel.py      # 协议测试面板（客户端+服务端+集合）
        ├── protocol_workers.py    # 协议测试 Worker 线程
        ├── target_panel.py        # 目标管理
        ├── test_panel.py          # 连通测试执行
        ├── result_panel.py        # 测试历史
        ├── table_utils.py         # 表格/树组件共用工具（列自适应、拖拽排序树）
        └── port_scan_dialog.py    # 端口扫描对话框
```

## 从源码编译打包

### Windows x64

```bash
pip install PySide6 openpyxl xlrd Pillow pyinstaller

pyinstaller --onefile --windowed --name TestTool \
    --icon=src/icon.ico \
    --add-data "src/icon.ico;portcheck" \
    --clean --noconfirm main.py
```

### Linux ARM64

```bash
sudo apt-get install -y libegl1 libgl1 libopengl0 libxkbcommon0
pip install PySide6 openpyxl xlrd Pillow pyinstaller

pyinstaller --onefile --windowed --name TestTool \
    --add-data "src/icon.png:portcheck" \
    --clean --noconfirm main.py
```

## GitHub Actions 自动构建

```bash
git tag v0.2.0
git push origin v0.2.0
```

## License

MIT
