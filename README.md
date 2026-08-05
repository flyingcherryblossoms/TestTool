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
- **TCP/WebSocket 客户端** — 连接参数自定义（编码/HeadLen/超时），名称字段独立，参数变更标签页显示 `*`
- **预设报文模板** — 添加/删除（支持多选）/重命名/清空，Ctrl+S 保存，未保存时标题显示 `*`
- **响应显示** — 接收编码选择 + 自动检测 + 十六进制/文本切换，同时显示请求和响应报文
- **Mock 服务端 / 服务端** — TCP/WebSocket 监听，固定/回显响应模式，列表含发送/接收编码与运行状态列，双击行编辑
- **日志优化** — 客户端/服务端日志统一格式 `[yyyy-MM-dd HH:mm:ss][角色][IP:端口]: 报文`，请求-回复用分隔符隔开
- **编码联动** — 运行中修改编码自动同步到服务列表与监听器
- **集合导入导出** — JSON 格式多集合批量导出/导入，含目标参数、预设报文、Mock 服务端完整配置
- **独立客户端** — 快速测试，参数自动持久化，Ctrl+S 保存预设或存到集合，保存后自动刷新集合列表
- **批量操作** — 服务端支持多选启动/停止/删除，列排序
- **测试历史** — 含请求/响应报文完整记录，刷新/删除/清空/导出（默认 xlsx），全局历史与集合历史一致

### 管理
- **集合管理** — 树形分类（未分类 + 自定义集合），搜索过滤，拖拽排序，默认建立未分类集合
- **集合操作** — 新建/编辑/删除/导入/导出，右键菜单集成，删除集合时目标自动移入未分类
- **集合详情** — 目标名称排序第一列，右键菜单含添加/测试/编辑/删除/全选/反选/刷新
- **筛选过滤** — 文本搜索 + 状态/类型筛选
- **列排序** — 点击列标题排序，箭头指示升降序
- **批量操作** — 集合/目标/服务端均支持多选批量操作

### 快捷键
- **F5** — 焦点在列表/表格时刷新当前数据
- **Delete / Ctrl+D** — 焦点在可删除数据时删除选中项
- **Ctrl+S** — 独立客户端：发送报文框聚焦时保存预设，否则保存到集合；集合详情客户端：发送报文框聚焦时保存预设，否则保存参数
- **IP/端口校验** — 编辑目标时自动校验 IPv4 格式和端口范围

### 导入导出
- **CSV/Excel/JSON** — 连通测试和协议测试均支持三种格式导入导出
- **JSON** — 支持多集合批量导出（单文件），多文件批量导入
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

- **客户端**：切换到「协议测试」→「客户端」tab，配置参数后发送；响应区选择接收编码，同时显示请求和响应
- **预设报文**：发送区「保存」或 Ctrl+S 保存当前报文，预设列表右键可添加/删除/重命名/清空
- **Mock 服务端 / 服务端**：添加监听器（双击行或右键可编辑），启动后独立日志 tab，日志显示实际请求/回复报文
- **集合测试**：左侧集合列表右键可添加目标/刷新/重命名/删除，双击目标打开详情页，详情页 tab 以名称或 ip:port 命名
- **独立客户端**：配置完成可点「保存到集合」存为集合中的目标
- **快捷键**：F5 刷新列表，Delete/Ctrl+D 删除选中，Ctrl+S 保存预设/参数

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
