# TestTool

跨平台网络测试工具。支持批量 TCP 连通性检测、TCP/WebSocket/HTTP 协议测试、客户端压测、IP/端口范围展开、端口扫描、集合管理，以及 CSV、Excel、TestTool JSON 和 Postman Collection 导入导出。

![Windows](https://img.shields.io/badge/Windows-x64-blue)
![Linux](https://img.shields.io/badge/Linux-x64%20%7C%20ARM64%20%7C%20Compat-orange)
![macOS](https://img.shields.io/badge/macOS-ARM64-silver)
![Python](https://img.shields.io/badge/Python-3.8.2+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

当前版本：[v1.1.1](https://github.com/flyingcherryblossoms/TestTool/releases/tag/v1.1.1)。

本次更新修复 HTTP 测试服务端返回报文的十六进制显示，保留实际发送的状态行、响应头和正文字节；客户端发送区、响应区及服务端报文区的「清空」按钮统一位于各自工具栏右侧。

## 功能特性

### 连通性测试

- **TCP 连通性检测** — 多线程并发（最高 200），实时进度，支持终止
- **超时精度** — 小数秒（0.1~60s），适配不同网络环境
- **目标列表** — 全选/反选/刷新，双击行直接测试该目标，测试完成自动刷新状态
- **右键菜单** — 添加/编辑/删除/协议测试/测试连通性，操作更快捷
- **IP 范围支持** — 单 IP、CIDR、范围、换行分隔，添加/编辑时合法性校验
- **端口范围支持** — 单端口、范围、逗号、换行、混合格式
- **端口扫描** — 扫描开放端口后导入集合
- **临时列表** — 协议测试选中目标可一键发送到连通测试临时列表，直接测试并显示最近状态，可一键保存到集合（去重）
- **高并发稳定性** — 批量写库 + 信号线程锁 + QThread 安全清理

### 协议测试

- **TCP/WebSocket/HTTP 客户端** — TCP 支持发送/接收编码、长度头和超时；WebSocket 支持 URL 和超时；HTTP 支持方法、URL、查询参数、请求头、正文、鉴权、Cookie、重定向和 SSL 校验；目标名称独立，参数变更标签页显示 `*`
- **预设报文模板** — 添加/删除（支持多选）/重命名/清空，Ctrl+S 保存，未保存时标题显示 `*`；每个预设独立的未保存草稿缓存，切换不丢失、互不影响，关闭程序时提示保存
- **报文收发记录** — 请求和响应逐条展示，可查看原报文、格式化正文或十六进制；运行日志可单独展开，TCP 支持接收编码选择
- **Mock 服务端 / 服务端** — TCP/WebSocket/HTTP 监听，固定/回显响应模式，列表含发送/接收编码与运行状态列，双击行编辑；每条服务端可配置多条命名「返回报文」，选中即当前返回，HTTP 模式每条返回报文自带状态码 + 响应头
- **目标详情左右分栏** — 客户端 | Mock服务端 左右并排，可拖动调整比例；客户端保留 620px 最小宽度，继续向左拖动可收起，并可用「显示客户端」恢复；服务端面板含搜索筛选、状态栏与完整功能按钮
- **连通性测试按钮** — 客户端直接检测当前 IP:端口并显示结果，无需跳转页面
- **日志优化** — 客户端/服务端日志统一格式 `[yyyy-MM-dd HH:mm:ss][角色][IP:端口]: 报文`，请求-回复用分隔符隔开
- **编码联动** — 运行中修改编码自动同步到服务列表与监听器
- **集合导入导出** — 右键导入、导出选中集合或全量导出；TestTool JSON 保存目标、预设、压测和 Mock 服务端完整配置，Postman Collection 用于 HTTP 请求交换
- **独立客户端** — 快速测试，参数自动持久化，Ctrl+S 保存预设或存到集合，保存后自动刷新集合列表
- **压测功能** — 连通性测试按钮旁「压力测试」按钮展开隐藏参数区：核心参数（并发数 / 总请求数 / QPS限制），进阶参数（压测时长 / 预热 / 超时 / 递增步长）；开始/停止/重置默认参数，实时进度与结果统计（成功/失败/耗时）；令牌桶 QPS 限速、预热期逐步递增并发，参数随目标存入数据库并随导入导出
- **报文编辑与格式化** — 纯文本编辑器支持 TEXT/JSON/XML 类型选择、JSON/XML 语法高亮、Ctrl+滚轮缩放和 Ctrl+Z 撤销；客户端点击「格式化」自动识别 JSON/XML，并同步格式选项和高亮
- **响应延迟** — Mock 服务端支持毫秒级延迟返回（0 表示不延迟），配置入库并随导入导出
- **返回报文列表** — 服务端面板主区域返回报文列表 + 输入框（逻辑参考客户端预设报文）：添加/重命名/删除/保存（Ctrl+S），当前返回项标记 ●，切换即热更新运行中的监听器；HTTP 服务端每条约含状态码 + 响应头表格
- **复制/剪贴板** — 预设、目标、服务端支持一键复制（名称自动追加"副本"），Ctrl+C/Ctrl+V 应用内剪贴板跨集合复制粘贴
- **批量操作** — 服务端支持多选启动/停止/删除，列排序
- **目标测试历史** — 在目标详情的「测试历史」页保存发送报文、响应报文和发送时的参数快照，失败原因单独记录；发送/接收详情并排展示，各自支持报文/16进制切换、TEXT/JSON/XML 类型选择及格式化；可搜索、筛选、排序、刷新、删除、清空和导入/导出 Excel/CSV/JSON，并可恢复为新配置再次测试。旧记录缺少参数时显示提示，未保存原始字节的记录按文本和编码转换为16进制
- **保存接收文件** — 服务端报文页选择一条接收记录后点击「保存接收文件」，TCP 优先保存原始报文体字节，WebSocket 保存接收报文，HTTP 保存请求正文；此功能保存所选报文，不解析 multipart 上传中的独立文件

### 管理

- **界面主题** — 菜单「设置 → 主题」提供浅色（增强区分）、经典浅色、暗色和高对比度，切换立即生效并记住选择
- **界面缩放与高 DPI** — Qt 自动适配高分屏；首次启动按屏幕可用尺寸选择比例，菜单「设置 → 界面缩放」可在 50%–300% 间立即调整并保存
- **CSP 报文解析** — 菜单「其他工具」打开，粘贴 Hex Dump 报文（自动忽略偏移地址、ASCII 列与 DataLength/--- 说明行），可手动选择/输入解码编码或自动检测（UTF-8/GBK/GB2312/GB18030/ISO-8859-1/ASCII），解决固定编码导致的中文乱码；解析后支持 JSON/XML/纯文本格式化并带语法高亮，校验提取字节数与 DataLength 声明是否一致
- **集合管理** — 树形分类（未分类 + 自定义集合），搜索过滤，拖拽排序，默认建立未分类集合；连通测试与协议测试共用同一集合组件（各自独立实例）
- **集合操作** — 新建/编辑/删除/复制以及导入导出均从右键菜单操作；支持 Ctrl/Shift 多选集合后「导出选中集合」，或「全量导出」（包含未分类）；空白/未分类/父节点均可刷新，删除集合时目标自动移入未分类，复制集合会复制其全部目标
- **应用内剪贴板** — Ctrl+C / Ctrl+V 在同类型列表间复制粘贴（预设、协议目标（含其下挂服务端）、服务端、集合、连通测试目标），跨集合/跨面板生效，名称或描述自动追加"副本"
- **拖拽归集** — 连通测试/协议测试的目标可直接拖到左侧集合树节点，移动目标到该集合
- **集合详情** — 目标名称排序第一列，右键菜单含添加/测试/编辑/删除/全选/反选/刷新，选中目标可一键发送到连通测试临时列表
- **筛选过滤** — 文本搜索 + 状态/类型筛选
- **列排序** — 点击列标题排序，箭头指示升降序
- **批量操作** — 集合/目标/服务端均支持多选批量操作

### 快捷键

- **Ctrl+Enter** — 焦点在客户端面板内（发送报文框/参数区/HTTP 编辑区）时发送报文
- **可配置** — 菜单栏「设置」打开快捷键设置：点击「快捷键」单元格后按下新组合键录制，Backspace 清空（禁用），Esc 取消，保存时自动校验跨功能冲突，支持逐项/全部恢复默认；绑定即时生效并持久化
- **F5** — 焦点在列表/表格时刷新当前数据
- **Delete / Ctrl+D** — 焦点在可删除数据时删除选中项
- **Ctrl+C / Ctrl+V** — 焦点在可复制列表时复制/粘贴选中项（应用内剪贴板）
- **Ctrl+S** — 独立客户端：发送报文框聚焦时保存预设，否则保存到集合；集合详情客户端：发送报文框聚焦时保存预设，否则保存参数
- **Ctrl+加号/减号** — 随时放大/缩小整个界面（Ctrl+= 也可放大），Ctrl+0 恢复 100%；报文编辑框内 Ctrl+滚轮只调整该编辑框字号
- **IP/端口校验** — 编辑目标时自动校验 IPv4 格式和端口范围

### 导入导出

| 数据 | 导入 | 导出 |
| --- | --- | --- |
| 连通测试目标 | CSV、Excel（xlsx/xls）、TestTool JSON、Postman Collection v2.0/v2.1 | CSV、Excel（xlsx）、TestTool JSON、Postman Collection v2.1 |
| 协议测试集合/目标 | TestTool JSON、Postman Collection v2.0/v2.1 | TestTool JSON、Postman Collection v2.1 |
| 目标协议测试历史 | Excel（xlsx）、CSV、JSON | Excel（xlsx）、CSV、JSON |

- **入口与范围** — 在集合树上右键选择「导入集合」「导出选中集合」或「全量导出」；导出对话框选择文件格式。协议测试支持一次导入多个 JSON 文件，多个选中集合可导出到同一文件。
- **TestTool JSON** — 保存协议目标参数、各协议预设、压测参数及目标关联的 Mock 服务端配置；需要完整备份时使用此格式。
- **Postman HTTP 请求** — 导入展开嵌套目录，并保留目录路径作为目标名称；转换方法、URL、查询参数、启用的请求头、JSON/XML/文本正文、文本表单、二进制文件路径，以及 Bearer/Basic/Digest/API Key 鉴权。支持集合/目录鉴权继承和集合变量替换；导出包含目标下全部 HTTP 预设。
- **Postman 连通目标** — 导入提取请求 URL 的主机和端口，HTTP/HTTPS 默认端口为 80/443；导出生成 GET 请求，443 使用 HTTPS，其余端口使用 HTTP，仅表示连通端点。
- **Postman 范围** — TCP/WS 目标、Mock 服务端和压测配置不包含在 Postman 文件中；没有 HTTP 预设的目标会提示跳过。脚本不导入或执行，未解析的环境变量保留为 `{{变量名}}` 并提示测试前替换；form-data 文件字段暂不支持。
- **默认文件名** — 使用「集合名称_yyyyMMddHHmmss」，多选取首集合名，默认导出 TestTool JSON。
- **连通目标导入** — 按集合、IP、端口去重；大批量导入提供二次确认、进度条和取消操作。

### 存储

- **SQLite** — 本地单文件，WAL 模式；源码运行默认使用项目目录的 `testtool.db`，打包程序默认使用用户主目录下的 `.config/TestTool/testtool.db`，Qt 用户设置保存为同目录的 `TestTool.ini`；首次启动时可自动迁移程序旁的旧数据库，已有用户库不会被覆盖，启动时可用 `--db` 指定路径；旧数据库自动补齐新增历史字段

## 安装与升级

所有发布构建使用 PyInstaller `--onedir`，程序和依赖安装到固定目录，启动时不解压单文件包。安装/升级/卸载不会删除用户主目录下的 `.config/TestTool`；`testtool.db` 保存集合、报文、历史及其他配置，`TestTool.ini` 保存 Qt 用户设置。

### Windows x86_64

下载安装文件 `TestTool-1.1.1-Windows-x64-Setup.exe` 并运行。开始菜单提供 TestTool 入口，安装器可选择创建桌面快捷方式；运行新版安装器即可覆盖升级，安装前按提示关闭正在运行的程序。也可下载目录版 ZIP，完整解压后运行 `TestTool/TestTool.exe`，请保留 `_internal` 依赖目录。

Windows 安装器仅包含 64 位 x86_64 程序，不提供 ARM64 或 32 位版本。用户配置位于 `%USERPROFILE%\.config\TestTool`，不会写入 Program Files。

### Debian / Ubuntu（x86_64、ARM64）

新版 deb 与标准 Linux 目录包使用相同的 Qt 和 Wayland 装饰插件，需要 glibc 2.39 及以上（如 Ubuntu 24.04）。旧系统可使用 `-compat.tar.gz` 目录包。早期 `1.0.7-1` 至 `1.0.7-5` deb 来自 Qt 6.6.3 兼容构建，与标准目录包不同。

根据 `dpkg --print-architecture` 选择 `amd64` 或 `arm64` 文件，使用 APT 安装并处理系统库依赖：

```bash
# x86_64
sudo apt install ./testtool_1.1.1-1_amd64.deb
# ARM64（在 ARM64 系统上安装）
sudo apt install ./testtool_1.1.1-1_arm64.deb
```

安装后从应用菜单启动，或运行 `testtool`。后续下载更高版本的同架构 deb，再执行 `sudo apt install ./新版文件.deb` 即可升级；这些下载包不会配置 APT 仓库，需要自行下载新版。

deb 同时支持 X11 和 Wayland 桌面环境。启动器不覆盖桌面的 XDG/Qt 环境变量，由 Qt 根据桌面环境选择显示后端，并保留用户显式设置的 `QT_QPA_PLATFORM` 和窗口装饰设置。键盘映射库统一使用系统的 libxkbcommon/libxkbcommon-x11，避免版本混用。

如需显式使用 X11 后端（包括 Wayland 桌面的 XWayland 会话），可运行：

```bash
QT_QPA_PLATFORM=xcb testtool
```

正式 deb 包要求 glibc 2.39 或更新版本及声明的 Qt/X11/Wayland 系统依赖，无需安装 Python。启动器保留桌面的 `XDG_CONFIG_HOME`，应用数据库和自身 Qt 设置仍位于 `~/.config/TestTool/`。程序目录为 `/usr/lib/testtool`，启动入口为 `/usr/bin/testtool`；可用 `testtool --db /path/to/testtool.db` 指定数据库。

```bash
sudo apt remove testtool
# 如需手动验证下载文件，在安装文件目录执行：
sha256sum -c testtool_1.1.1-1_amd64.deb.sha256
```

## 使用方法

### 源码运行

```bash
pip install PySide6 openpyxl xlrd websocket-client websockets requests
python main.py
python main.py --db /path/to/testtool.db
```

使用 uv 时，可执行 `uv sync --locked`，再用项目虚拟环境中的 Python 启动 `main.py`。

### 连通性测试

- **按集合测试**：左侧选集合 → 切换到「连通测试」→ 勾选目标 → 点击开始
- **快速测试**：目标列表中双击任意目标行即可测试该条连通性
- **终止测试**：测试中点击「⏹ 停止测试」
- **实时筛选**：结果表格支持文本搜索 + 状态筛选

### 协议测试

- **客户端**：切换到「协议测试」→「客户端」tab，配置参数后发送；响应区选择接收编码，同时显示请求和响应
- **预设报文**：发送区「保存」或 Ctrl+S 保存当前报文，预设列表右键可添加/删除/重命名/清空
- **Mock 服务端 / 服务端**：添加监听器（类型可选 TCP/WebSocket/HTTP，双击行或右键可编辑），启动后在独立报文页查看实际请求/回复，按需展开日志；选中行后在下方「返回报文」区编辑当前服务端返回内容（HTTP 服务端含状态码/响应头/内容，选中列表项即当前返回，切换/保存即时生效）
- **集合测试**：左侧集合列表右键可添加目标/刷新/重命名/删除，双击目标打开详情页，详情页 tab 以名称或 ip:port 命名
- **独立客户端**：配置完成可点「保存到集合」存为集合中的目标
- **压测**：发送区「压力测试」按钮展开参数区，填好并发数/总请求数等后点「开始压测」，过程中可「停止」，结果实时统计成功/失败/耗时；「重置默认参数」一键恢复默认值
- **报文格式**：发送报文框旁选择 TEXT/JSON/XML；点击「格式化」自动识别 JSON/XML 并更新格式选项。服务端响应框可在「内容格式」下拉选择
- **测试历史**：双击集合中的目标 →「测试历史」→ 选择记录；左右分别查看发送/接收报文及当时参数，独立切换报文/16进制或格式化显示

历史参数只显示该次测试所选协议：TCP 不显示 WS/HTTP 配置，WS 不显示 TCP/HTTP 配置。发送时冻结协议与参数，之后切换协议不会改变历史；已有混合参数的旧记录在查看、搜索和导出时也按记录协议筛选。

选中测试历史后，点击「导入为新配置」（或右键同名菜单），即可创建独立配置并切换到客户端，恢复该次测试的协议、参数和发送报文；点击「发送」可再次测试。重复导入会使用不同名称，不覆盖已有配置。缺少参数快照的旧记录会提示无法导入。

测试历史工具栏和右键菜单中的「导入」可读取程序导出的 Excel/CSV/JSON 文件，将记录追加到当前目标，保留原测试时间、协议、结果、报文、参数和原始字节。支持旧版 Excel/CSV 导出列；文件先完整校验，格式错误不会部分写入。导出时有选中记录则导出选中项，否则导出当前筛选结果。报文超过 Excel 单元格文本限制或含控制字符时，请选择 CSV/JSON。
- **保存接收文件**：启动服务端并收到报文后，在其报文列表选中一条「接收」记录 →「保存接收文件」→ 选择保存路径
- **Postman 交换**：集合树右键「导入集合」选择 Postman JSON；导出时选择「Postman Collection v2.1」。目标详情也可导入/导出 Postman 请求，导入多个请求时会提示选择
- **快捷键**：Ctrl+Enter 发送，F5 刷新列表，Delete/Ctrl+D 删除选中，Ctrl+S 保存预设/参数，Ctrl+C/Ctrl+V 复制粘贴列表项；菜单栏「设置」可查看/修改全部快捷键

### CSP 报文解析

- **入口**：菜单「其他工具」→「CSP 报文解析...」
- **用法**：粘贴 Hex Dump 报文（仅取十六进制部分，偏移/ASCII/说明行自动忽略），选择解码编码或「自动检测」，点击「解析」；编码选错时同样展示容错解码结果（无效字节替换为 �，状态栏橙色提示），切换编码即可对比正确结果
- **格式化**：输出格式可选「自动识别 / JSON / XML / 纯文本」，可对已解析文本随时重排，结果自动按内容语法高亮（JSON 键/字符串/数字/布尔，XML 标签/属性）；Ctrl+Enter 快捷解析

## 项目结构

```
TestTool/
├── main.py                        # 入口（GUI + CLI）
├── pyproject.toml                 # 项目版本与运行依赖
├── uv.lock                        # uv 依赖锁定
├── packaging/                     # deb 构建脚本与 Windows Inno Setup 安装器
├── tests/                         # 转换、安装升级和用户目录回归测试
└── src/
    ├── database.py                # SQLite 数据层
    ├── scanner.py                 # TCP 并发检测引擎 + IP/端口展开
    ├── protocol.py                # TCP/WS/HTTP 协议引擎（收发/服务端）
    ├── csv_handler.py             # CSV 导入导出
    ├── excel_handler.py           # Excel 导入导出
    ├── json_handler.py            # JSON 协议配置导入导出
    ├── postman_handler.py         # Postman Collection 与 HTTP/连通目标互转
    └── ui/
        ├── main_window.py         # 主窗口
        ├── connectivity_panel.py  # 连通测试面板（集合+目标+测试+历史）
        ├── protocol_panel.py      # 协议测试面板（客户端+服务端+集合）
        ├── protocol_components.py # 协议客户端/服务端公共组件（ClientPanelBase / ServerPanelBase）
        ├── server_response_panel.py # 服务端返回报文列表 + 输入框（ResponseMessageSection）
        ├── collection_sidebar.py  # 集合管理侧栏组件（连通/协议共用）
        ├── protocol_workers.py    # 协议测试 Worker 线程（含压测 StressTestWorker）
        ├── format_text.py         # 纯文本报文编辑组件（格式选择/语法高亮/缩放）
        ├── message_format.py      # 报文格式化（text/json/xml 缩进排版）
        ├── message_history.py     # 收发报文列表、详情与可选运行日志
        ├── clipboard.py           # 应用内剪贴板（Ctrl+C/Ctrl+V 列表项复制粘贴）
        ├── shortcuts.py           # 快捷键注册表（默认绑定 + 加载/保存 + keyPressEvent 匹配 + QShortcut 热更新）
        ├── shortcut_settings_dialog.py  # 快捷键设置对话框（录制/校验冲突/恢复默认）
        ├── target_panel.py        # 目标管理（含临时列表）
        ├── test_panel.py          # 连通测试执行
        ├── result_panel.py        # 测试历史
        ├── table_utils.py         # 表格/树组件共用工具（列自适应、拖拽排序树）
        ├── port_scan_dialog.py    # 端口扫描对话框
        └── csp_parser_dialog.py   # CSP 报文解析对话框（Hex Dump 提取 + 编码解码 + 格式化）
```

## 从源码编译打包

### Windows x64

```bash
pip install PySide6 openpyxl xlrd websocket-client websockets requests pyinstaller

pyinstaller --onedir --windowed --name TestTool \
    --icon=resources/icon.ico \
    --add-data "resources/icon.ico;resources" \
    --clean --noconfirm main.py
```

### Linux ARM64

```bash
sudo apt-get install -y libegl1 libgl1 libopengl0 libxkbcommon0 libxcb-cursor0
pip install PySide6 openpyxl xlrd websocket-client websockets requests pyinstaller

pyinstaller --onedir --windowed --name TestTool \
    --add-data "resources/icon.png:resources" \
    --clean --noconfirm main.py
```

### 生成 Windows 安装器

先在 Windows 上生成 `dist/TestTool/` 目录包，再安装 Inno Setup 6，运行：

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DMyAppVersion=1.1.1 packaging/windows-installer.iss
```

输出为 `dist/TestTool-1.1.1-Windows-x64-Setup.exe`。安装器的应用 ID 不随版本改变，用于后续覆盖升级。

### 生成 deb 安装包

先在对应架构的 Linux 系统上生成目录包，再运行（ARM64 使用 `--arch arm64`）：

```bash
python packaging/build_deb.py --bundle dist/TestTool --arch amd64 --minimum-glibc 2.39
```

脚本拒绝单文件构建和架构错标，输出 deb 与 SHA256 文件。使用相同包名 `testtool` 和递增版本号支持升级；CI 在 x86_64/ARM64 原生运行器上分别生成目录包。

`--minimum-glibc` 接受数字主/次版本号，默认 `2.31`，需与目录包的实际构建环境及依赖要求匹配；此参数只设置 deb 的 `libc6` 依赖，不会降低二进制的 glibc 要求。打包时仅从 deb 暂存目录删除 libxkbcommon/libxkbcommon-x11 的库文件和符号链接，保留其他 Qt/Wayland 库及原始目录包，并声明系统键盘映射库和 `xkb-data` 依赖。

## 测试

回归检查使用临时文件和数据库，覆盖 Postman 转换、配置迁移、协议历史参数筛选、历史恢复为新配置、CSV/Excel/JSON 往返，以及 deb 安装升级保留用户数据。

```bash
pip install pytest
python -m pytest -q tests/test_postman_handler.py tests/test_runtime_paths.py
# Linux 打包/升级检查还需要 dpkg-deb、fakeroot：
python -m pytest -q tests/test_deb_package.py
# Linux 完整回归检查（含 Qt 控件，使用无桌面显示后端）：
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

界面和网络协议功能仍需运行应用进行验证；测试时可通过 `--db` 指向临时数据库。

## GitHub Actions 自动构建

推送新的 `v*` tag 会触发 Windows x64、Linux x64/ARM64（含 Python 3.8 兼容构建）和 macOS ARM64 打包，并发布 GitHub Release，包含 Windows x86_64 安装器、两种架构 deb 和目录版归档。也可手动运行 Build 工作流生成可下载的 Actions 构建产物。先同步 `pyproject.toml` 与 `uv.lock` 中的版本，再提交、创建新 tag 并推送，例如：

```bash
git tag -a v1.1.1 -m "Release v1.1.1"
git push origin main v1.1.1
```

## License

MIT
