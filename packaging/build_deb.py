"""将 Linux PyInstaller 目录构建打包为可升级的 amd64/arm64 deb。"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MINIMUM_GLIBC = '2.31'
DEPENDENCIES = (
    'libstdc++6, libgcc-s1, libegl1, libgl1, libopengl0, '
    'libdbus-1-3, libx11-6, libx11-xcb1, libxext6, libxrender1, libxi6, '
    'libxkbcommon0, libxkbcommon-x11-0, libxcb1, libxcb-cursor0, '
    'libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, '
    'libxcb-render-util0, libxcb-render0, libxcb-shape0, libxcb-shm0, '
    'libxcb-sync1, libxcb-xfixes0, libxcb-xinerama0, libxcb-xkb1, '
    'libxcb-util1, libfontconfig1, libfreetype6, libwayland-client0, '
    'libwayland-cursor0, libwayland-egl1, xkb-data'
)
LAUNCHER = '''#!/bin/sh
set -eu
# 应用自身负责配置目录，不覆盖桌面的 XDG_CONFIG_HOME（主题/窗口装饰配置）。
# 不修改 Qt 显示后端和装饰设置，保持与直接运行二进制相同的行为。
# 显式 --db 参数保持原样；安装/升级不会覆盖数据库。
for argument in "$@"; do
    case "$argument" in
        --db|--db=*|-h|--help) exec /usr/lib/testtool/TestTool "$@" ;;
    esac
done
umask 077
config_dir="$HOME/.config/TestTool"
mkdir -p "$config_dir"
exec /usr/lib/testtool/TestTool --db "$config_dir/testtool.db" "$@"
'''
DESKTOP = '''[Desktop Entry]
Type=Application
Name=TestTool
Name[zh_CN]=TestTool 网络测试工具
Comment=TCP, WebSocket and HTTP network testing
Comment[zh_CN]=TCP 连通性检测与 WebSocket、HTTP 协议测试
Exec=testtool
Icon=testtool
Terminal=false
Categories=Network;
StartupNotify=true
'''


def validate_binary(binary: Path, architecture: str):
    """拒绝架构错标和非 Linux ELF，不能用 x86 二进制伪装 ARM 包。"""
    with binary.open('rb') as source:
        header = source.read(20)
    if len(header) != 20 or header[:4] != b'\x7fELF' or header[4:6] != b'\x02\x01':
        raise ValueError('需要 Linux 64 位小端 ELF 可执行文件')
    machine = struct.unpack('<H', header[18:20])[0]
    if machine != {'amd64': 62, 'arm64': 183}[architecture]:
        raise ValueError('二进制架构与 --arch 不符')


def project_version() -> str:
    if os.environ.get('GITHUB_REF_TYPE') == 'tag':
        return os.environ['GITHUB_REF_NAME'].lstrip('v')
    match = re.search(r'^version\s*=\s*"([^"]+)"',
                      (ROOT / 'pyproject.toml').read_text(), re.MULTILINE)
    if not match:
        raise ValueError('无法读取项目版本')
    return match.group(1)


def write_file(path: Path, content: str, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    path.chmod(mode)


def build_deb(bundle: Path, architecture: str, version: str, output_dir: Path,
              maintainer: str = 'Quasimodo <wangaimeng@outlook.com>',
              minimum_glibc: str = DEFAULT_MINIMUM_GLIBC) -> Path:
    # 仅接受主/次版本号，防止无效版本或控制字段注入；默认值与 CLI 共用。
    if not isinstance(minimum_glibc, str) or not re.fullmatch(r'[0-9]+\.[0-9]+', minimum_glibc):
        raise ValueError('最低 glibc 版本应为数字版本号，如 2.39')
    dependencies = f'libc6 (>= {minimum_glibc}), {DEPENDENCIES}'
    binary = bundle / 'TestTool'
    if not bundle.is_dir() or not (bundle / '_internal').is_dir():
        raise ValueError('需要 PyInstaller --onedir 生成的目录（含 TestTool 和 _internal），不接受单文件')
    validate_binary(binary, architecture)
    version = version.lstrip('v')
    # Debian revision 表示同一应用版本的打包修订；后续较大版本可直接升级。
    if '-' not in version:
        version += '-1'
    subprocess.run(['dpkg', '--validate-version', version], check=True)
    if '\n' in maintainer or not re.fullmatch(r'.+ <[^<>\s]+@[^<>\s]+>', maintainer):
        raise ValueError('Maintainer 必须是名称及邮箱，且不能包含换行')
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir.resolve() / f'testtool_{version}_{architecture}.deb'
    with tempfile.TemporaryDirectory(prefix='testtool-deb-') as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        installed_dir = stage / 'usr/lib/testtool'
        installed_dir.parent.mkdir(parents=True)
        shutil.copytree(bundle, installed_dir, symlinks=True)
        # xkbcommon-x11 由系统提供；核心库也必须来自同一系统版本，避免 ABI 混用。
        for library in installed_dir.rglob('libxkbcommon*.so*'):
            if (re.fullmatch(r'libxkbcommon(?:-x11)?\.so(?:\..+)?', library.name)
                    and (library.is_file() or library.is_symlink())):
                library.unlink()
        installed_binary = installed_dir / 'TestTool'
        installed_binary.chmod(0o755)
        # 保留目录包内链接，但拒绝指向包外的依赖和数据文件。
        for path in installed_dir.rglob('*'):
            if path.is_symlink():
                try:
                    path.resolve(strict=True).relative_to(installed_dir.resolve())
                except (ValueError, FileNotFoundError):
                    raise ValueError('程序目录含无效或指向包外的符号链接: ' + str(path))
            elif path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        write_file(stage / 'usr/bin/testtool', LAUNCHER, 0o755)
        write_file(stage / 'usr/share/applications/testtool.desktop', DESKTOP)
        icon = stage / 'usr/share/icons/hicolor/256x256/apps/testtool.png'
        icon.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'resources/icon.png', icon)
        icon.chmod(0o644)
        write_file(stage / 'usr/share/doc/testtool/README.Debian',
                   'Start from the application menu or run: testtool\n'
                   'Default database and Qt user configuration: ~/.config/TestTool/ (testtool.db / TestTool.ini)\n'
                   'Use testtool --db /path/to/testtool.db to select an existing database.\n'
                   'Upgrading or removing the package preserves per-user data.\n'
                   'The package includes Python/Qt; a system Python installation is not required.\n')
        payload = sorted(path for path in stage.rglob('*') if path.is_file() and not path.is_symlink())
        installed_size = sum((path.stat().st_size + 1023) // 1024 for path in payload)
        md5_lines = []
        for path in payload:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            md5_lines.append(f'{digest}  {path.relative_to(stage).as_posix()}\n')
        write_file(stage / 'DEBIAN/md5sums', ''.join(md5_lines))
        write_file(stage / 'DEBIAN/control',
                   f'Package: testtool\nVersion: {version}\nArchitecture: {architecture}\n'
                   f'Maintainer: {maintainer}\nSection: net\nPriority: optional\n'
                   f'Installed-Size: {installed_size}\nDepends: {dependencies}\n'
                   'Homepage: https://github.com/flyingcherryblossoms/TestTool\n'
                   'Description: desktop network connectivity and protocol testing tool\n'
                   ' Batch TCP checks, TCP/WebSocket/HTTP clients and mock servers,\n'
                   ' collections, stress testing and Postman import/export.\n')
        # 不使用安装脚本和系统级数据库；无需 root 即可创建标准 root 所有的包。
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build',
                        str(stage), str(output)], check=True)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    write_file(output.with_name(output.name + '.sha256'), f'{checksum}  {output.name}\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'dist/TestTool')
    parser.add_argument('--arch', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--version', default=None)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    parser.add_argument('--maintainer', default='Quasimodo <wangaimeng@outlook.com>')
    parser.add_argument('--minimum-glibc', default=DEFAULT_MINIMUM_GLIBC,
                        help=f'最低 glibc 主/次版本号；默认 {DEFAULT_MINIMUM_GLIBC}（Ubuntu 20.04 兼容构建），Ubuntu 24.04 构建使用 2.39')
    args = parser.parse_args()
    try:
        result = build_deb(args.bundle, args.arch, args.version or project_version(),
                           args.output_dir, args.maintainer, minimum_glibc=args.minimum_glibc)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + '\n')
    print(result)


if __name__ == '__main__':
    main()
