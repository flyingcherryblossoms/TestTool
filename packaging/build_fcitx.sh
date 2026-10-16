#!/bin/sh
# 用与 PySide6 相同版本的 Qt 私有头文件构建 fcitx4 插件，兼容搜狗输入法。
# 仅复制插件到 wheel；不把构建用 Qt 库混入发布包。
set -eu
python_bin="${1:-python3}"
qt_version=$("$python_bin" -c 'from PySide6.QtCore import qVersion; print(qVersion())')
qt_plugins=$("$python_bin" -c 'from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.PluginsPath))')
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
cd "$work_dir"
curl -fL --retry 3 "https://download.qt.io/archive/qt/${qt_version%.*}/$qt_version/submodules/qtbase-everywhere-src-$qt_version.tar.xz" -o qtbase.tar.xz
tar xf qtbase.tar.xz
mkdir qt-build
cd qt-build
# 仅需 Core/Gui/DBus 和相应开发工具；不构建 Widgets 等应用模块。
../qtbase-everywhere-src-"$qt_version"/configure -prefix "$work_dir/qt" \
    -release -opensource -confirm-license -nomake examples -nomake tests \
    -no-opengl -no-feature-vulkan -no-feature-widgets -no-feature-sql \
    -no-feature-network -no-feature-testlib -no-feature-concurrent
cmake --build . --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
cmake --install .
cd "$work_dir"
git clone --depth 1 --branch 1.2.7 https://github.com/fcitx/fcitx-qt5.git
# 通过 Qt 私有目标传递头文件，不依赖新版 Qt 中为空的旧变量。
# 同时适用于旧 Qt，避免 moc 找不到插件 IID 定义。
cat >> fcitx-qt5/qt6/platforminputcontext/CMakeLists.txt <<'CMAKE'

target_link_libraries(fcitxplatforminputcontextplugin-qt6 Qt6::GuiPrivate)
CMAKE
# 随二进制保留已修补的上游源代码及许可证。
mkdir -p "$qt_plugins/fcitx4-source"
tar --exclude=.git -cJf "$qt_plugins/fcitx4-source/fcitx-qt5-1.2.7.tar.xz" fcitx-qt5
cp fcitx-qt5/COPYING "$qt_plugins/fcitx4-source/"
cmake -S fcitx-qt5 -B fcitx-build -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$work_dir/qt" \
    -DENABLE_QT5=OFF -DENABLE_QT6=ON -DENABLE_LIBRARY=OFF
cmake --build fcitx-build --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
mkdir -p "$qt_plugins/platforminputcontexts"
cp fcitx-build/qt6/platforminputcontext/libfcitxplatforminputcontextplugin-qt6.so \
    "$qt_plugins/platforminputcontexts/"
# 使用随包 Qt 实际加载插件，发现私有 ABI 或依赖不兼容时终止发布。
"$python_bin" - "$qt_plugins/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so" <<'PY'
import sys
from PySide6 import QtGui, QtDBus  # 先加载 wheel 内的库，避免误用构建目录的 Qt
from PySide6.QtCore import QCoreApplication, QPluginLoader
app = QCoreApplication([])
loader = QPluginLoader(sys.argv[1])
if not loader.load():
    raise SystemExit(loader.errorString())
if 'fcitx' not in loader.metaData().get('MetaData', {}).get('Keys', []):
    raise SystemExit('fcitx input context key missing')
PY
