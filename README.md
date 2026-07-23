# PortCheck

跨平台网络端口连通性检测工具。支持批量 TCP 测试、IP/端口范围展开、端口扫描、集合管理，CSV/Excel 导入导出。

![Platform](https://img.shields.io/badge/Windows-x64-blue)
![Platform](https://img.shields.io/badge/Linux-ARM64-orange)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 截图

```
┌────────────────────────────────────────────────────────────┐
│  菜单栏: 文件(导入/导出/端口扫描)  帮助                  │
├────────────┬───────────────────────────────────────────────┤
│ 集合列表    │ [目标管理] [连通测试] [测试历史]             │
│            │                                               │
│ 🌐 全部    │ 筛选: [_____] 状态: [全部 ▼]                 │
│ 📁 生产    │ ┌──────────────────────────────────────────┐ │
│ 📁 测试    │ │ # │ ☐ │ IP         │ Port │ 描述  │ 状态 │ │
│            │ │ 1 │ ☐ │ 192.168.1.1│ 22   │ Git.. │  ✓  │ │
│ [+新建]    │ │ 2 │ ☐ │ 10.0.0.1   │ 80   │ Web   │  ✗  │ │
│ [编辑]     │ └──────────────────────────────────────────┘ │
│ [删除]     │ [添加] [编辑] [删除] [▶测试选中] [导入/导出] │
├────────────┴───────────────────────────────────────────────┤
│ 状态栏: 共 15 个目标 / 3 个集合  |  上次测试: 2026-07-23  │
└────────────────────────────────────────────────────────────┘
```

## 功能特性

### 核心
- **TCP 连通性测试** — 多线程并发检测，实时进度显示
- **IP 范围支持** — 单 IP、CIDR (`10.0.0.0/24`)、范围 (`192.168.1.1-10`)
- **端口范围支持** — 单端口、范围 (`1-100`)、逗号 (`80,443`)、混合 (`80,8000-8010`)
- **端口扫描** — 扫描指定 IP 范围的端口，发现开放端口后导入集合

### 管理
- **集合管理** — 将目标按场景分组（生产/测试/数据库等）
- **拖拽排序** — 集合列表和目标表格均支持拖拽排序
- **筛选排序** — 按 IP/端口/描述/集合筛选，按列点击排序（IP 自然排序）
- **状态筛选** — 筛选连通/未连通/未测试的目标
- **勾选测试** — 勾选部分目标一键测试

### 导入导出
- **CSV** — 逗号分隔，UTF-8 BOM，兼容 Excel
- **Excel** — `.xlsx` / `.xls`，带样式（表头蓝色、连通绿色、失败红色）
- **防重** — 以「集合 + IP + 端口」判定重复，支持覆盖已有数据

### 存储
- **SQLite** — 本地单文件存储，WAL 模式
- 关闭后数据不丢失（保存在 exe 同目录）

## 使用方法

### Windows

下载 `PortCheck.exe`，双击运行。

### Linux ARM64

```bash
chmod +x PortCheck
./PortCheck
```

### 源码运行

```bash
# 安装依赖
pip install PySide6 openpyxl xlrd Pillow

# 启动 GUI
python main.py

# 命令行模式
python main.py --cli 192.168.1.1 22 10.0.0.1 80 --timeout 3
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--db PATH` | 指定数据库路径（默认 exe 同目录） |
| `--cli IP1 PORT1 [IP2 PORT2 ...]` | 命令行模式，IP/Port 成对 |
| `--timeout SEC` | 超时秒数（默认 3） |

### 添加目标

1. 点击「添加目标」，输入 IP 和端口
2. IP 支持：`192.168.1.1`、`192.168.1.0/24`、`192.168.1.1-10`
3. 端口支持：`80`、`1-1000`、`80,443,8080`
4. 输入框右侧实时显示将创建的条目数
5. 选择所属集合，确定即可

### 端口扫描

1. 菜单 `文件 → 端口扫描`
2. 输入目标 IP（支持范围）和端口范围（如 `1-1000`）
3. 点击扫描，实时显示开放端口
4. 勾选需要的端口 → 导入到指定集合

### 测试连通性

- **按集合测试**：左侧选集合 → 切换到「连通测试」→ 点击开始
- **勾选测试**：目标管理中勾选 → 点击「▶ 测试选中」
- 结果实时显示，完成后可筛选导出

## 从源码编译打包

### 生成图标

```bash
python -c "
from PIL import Image, ImageDraw
s=256;m=s/2
img=Image.new('RGBA',(s,s),(0,0,0,0))
d=ImageDraw.Draw(img)
d.rounded_rectangle([0,0,s-1,s-1],radius=s//5,fill='#1a5276')
pad=int(s*0.15)
d.rounded_rectangle([pad,pad,s-pad,s-pad],radius=s//6,fill='#2980b9')
cx,cy,nr=m,m*0.88,int(s*0.07)
nodes=[(cx,int(cy-s*0.22)),(int(cx-s*0.2),int(cy+s*0.12)),(int(cx+s*0.2),int(cy+s*0.12))]
for i in range(3):
 for j in range(i+1,3):d.line([nodes[i],nodes[j]],fill='#aed6f1',width=5)
for nx,ny in nodes:d.ellipse([nx-nr,ny-nr,nx+nr,ny+nr],fill='#ecf0f1')
tx,ty,ts=int(m+s*0.22),int(m+s*0.22),s*0.26
p1,p2,p3=(tx-int(ts*0.45),ty-int(ts*0.05)),(tx-int(ts*0.05),ty+int(ts*0.35)),(tx+int(ts*0.5),ty-int(ts*0.3))
d.line([p1,p2,p3],fill='#2ecc71',width=10,joint='curve')
img.save('portcheck/icon.ico',format='ICO',sizes=[(16,16),(32,32),(48,48),(256,256)])
img.save('portcheck/icon.png')
"
```

### Windows x64

```bash
pip install PySide6 openpyxl xlrd Pillow pyinstaller

pyinstaller --onefile --windowed --name PortCheck \
    --icon=portcheck/icon.ico \
    --add-data "portcheck/icon.ico;portcheck" \
    --clean --noconfirm main.py

# 输出: dist/PortCheck.exe
```

### Linux ARM64

```bash
# 安装 Qt 系统依赖
sudo apt-get install -y libegl1 libgl1 libopengl0 libxkbcommon0

pip install PySide6 openpyxl xlrd Pillow pyinstaller

pyinstaller --onefile --windowed --name PortCheck \
    --add-data "portcheck/icon.png:portcheck" \
    --clean --noconfirm main.py

# 输出: dist/PortCheck
```

## GitHub Actions 自动构建

推送代码后自动构建 Windows x64 和 Linux ARM64 包。推送 tag（如 `v1.0`）自动创建 Release。

```bash
git tag v1.0.0
git push origin v1.0.0
```

## 项目结构

```
PortCheck/
├── main.py                        # 入口（GUI + CLI 双模式）
├── requirements.txt               # PySide6
└── portcheck/
    ├── database.py                # SQLite 数据层
    ├── scanner.py                 # TCP 并发检测引擎 + IP/端口范围展开
    ├── csv_handler.py             # CSV 导入/导出
    ├── excel_handler.py           # Excel (xlsx/xls) 导入/导出
    └── ui/
        ├── main_window.py         # 主窗口
        ├── target_panel.py        # 目标管理（筛选/排序/拖拽/勾选）
        ├── test_panel.py          # 连通测试（实时进度）
        ├── result_panel.py        # 测试历史（多选删除/筛选导出）
        └── port_scan_dialog.py    # 端口扫描对话框
```

## 技术栈

| 层面 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| GUI | PySide6 (Qt) |
| 存储 | SQLite (WAL 模式) |
| 并发 | ThreadPoolExecutor + QThread |
| 表格 | openpyxl / xlrd |
| 打包 | PyInstaller |

## License

MIT
