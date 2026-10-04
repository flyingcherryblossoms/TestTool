"""Debian 打包、升级及用户数据目录行为回归检查。"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    'testtool_deb', Path(__file__).resolve().parents[1] / 'packaging/build_deb.py')
deb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deb)


def make_launcher(tmp_path):
    mock = tmp_path / 'mock app'
    mock.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                    'open(os.environ["TEST_ARGS"],"w").write(json.dumps(sys.argv[1:]))\n'
                    'keys = ["QT_QPA_PLATFORM", "QT_WAYLAND_DISABLE_WINDOWDECORATION", '
                    '"QT_QPA_PLATFORMTHEME", "QT_WAYLAND_DECORATION", "XDG_CONFIG_HOME", "DISPLAY", "WAYLAND_DISPLAY"]\n'
                    'open(os.environ["TEST_ARGS"]+".environment","w").write('
                    'json.dumps({key: os.environ.get(key) for key in keys}))\n')
    mock.chmod(0o755)
    launcher = tmp_path / 'testtool'
    launcher.write_text(deb.LAUNCHER.replace('/usr/lib/testtool/TestTool', shlex.quote(str(mock))))
    launcher.chmod(0o755)
    environment = dict(os.environ, HOME=str(tmp_path / 'home'),
                       XDG_DATA_HOME=str(tmp_path / 'data with spaces'),
                       TEST_ARGS=str(tmp_path / 'arguments'))
    return launcher, environment


def test_launcher_user_data_and_explicit_database(tmp_path):
    launcher, environment = make_launcher(tmp_path)
    subprocess.run([str(launcher)], env=environment, check=True)
    args = json.loads(Path(environment['TEST_ARGS']).read_text())
    assert args == ['--db', str(tmp_path / 'home/.config/TestTool/testtool.db')]
    assert (tmp_path / 'home/.config/TestTool').is_dir()
    database = tmp_path / 'home/.config/TestTool/testtool.db'
    database.write_bytes(b'existing-user-data')
    subprocess.run([str(launcher)], env=environment, check=True)
    assert database.read_bytes() == b'existing-user-data'
    for override in [['--db', '/tmp/custom db'], ['--db=/tmp/custom.db']]:
        subprocess.run([str(launcher)] + override, env=environment, check=True)
        assert json.loads(Path(environment['TEST_ARGS']).read_text()) == override


def test_relative_xdg_and_help(tmp_path):
    launcher, environment = make_launcher(tmp_path)
    environment['XDG_DATA_HOME'] = 'relative-data'
    environment['XDG_CONFIG_HOME'] = 'relative-config'
    subprocess.run([str(launcher)], env=environment, check=True)
    assert json.loads(Path(environment['TEST_ARGS']).read_text())[1] == str(tmp_path / 'home/.config/TestTool/testtool.db')
    assert json.loads(Path(environment['TEST_ARGS'] + '.environment').read_text())['XDG_CONFIG_HOME'] == 'relative-config'
    shutil.rmtree(tmp_path / 'home')
    subprocess.run([str(launcher), '--help'], env=environment, check=True)
    assert not (tmp_path / 'home').exists()
    assert json.loads(Path(environment['TEST_ARGS']).read_text()) == ['--help']


@pytest.mark.parametrize('display,wayland,requested', [
    (':0', None, None), (':0', 'wayland-0', None), (None, 'wayland-0', None),
    (':0', 'wayland-0', 'xcb'), (':0', None, 'offscreen'),
    (None, None, None), (':0', 'wayland-0', ''),
])
@pytest.mark.parametrize('arguments', [[], ['--help'], ['--db', '/tmp/custom db']])
def test_launcher_preserves_desktop_environment(tmp_path, display, wayland, requested, arguments):
    launcher, environment = make_launcher(tmp_path)
    expected = {
        'DISPLAY': display, 'WAYLAND_DISPLAY': wayland, 'QT_QPA_PLATFORM': requested,
        'QT_WAYLAND_DISABLE_WINDOWDECORATION': '0' if requested is not None else None,
        'QT_QPA_PLATFORMTHEME': 'custom-theme' if requested is not None else None,
        'QT_WAYLAND_DECORATION': 'adwaita' if requested is not None else None,
        'XDG_CONFIG_HOME': str(tmp_path / 'desktop config') if requested is not None else None,
    }
    for key, value in expected.items():
        environment.pop(key, None)
        if value is not None:
            environment[key] = value
    subprocess.run([str(launcher)] + arguments, env=environment, check=True)
    assert json.loads(Path(environment['TEST_ARGS'] + '.environment').read_text()) == expected


def test_wrong_architecture_rejected():
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    wrong = 'arm64' if architecture == 'amd64' else 'amd64'
    try:
        deb.validate_binary(Path('/bin/true'), wrong)
    except ValueError:
        return
    raise AssertionError('架构错标未被拒绝')


def make_bundle(path, executable):
    path.mkdir()
    shutil.copyfile(executable, path / 'TestTool')
    (path / '_internal').mkdir()
    (path / '_internal/dependency').write_text('directory bundle dependency')
    (path / '_internal/libxkbcommon.so.0').write_bytes(b'bundled-core')
    (path / '_internal/libxkbcommon-x11.so.0').write_bytes(b'bundled-x11')
    return path


def test_single_file_bundle_rejected(tmp_path):
    try:
        deb.build_deb(Path('/bin/true'), 'amd64', '1.0.6', tmp_path)
    except ValueError:
        return
    raise AssertionError('单文件包未被拒绝')


@pytest.mark.parametrize('minimum_glibc', ['', '2', '2.31.1', ' 2.31', '2.31 ',
                                          '2.31\n', '2.31), injected', '２.３１', None])
def test_invalid_minimum_glibc_rejected(tmp_path, minimum_glibc):
    output = tmp_path / 'packages'
    with pytest.raises(ValueError, match='glibc'):
        deb.build_deb(tmp_path / 'unused-bundle', 'amd64', '1.0.7', output,
                      minimum_glibc=minimum_glibc)
    assert not output.exists()


def test_cli_invalid_minimum_glibc_rejected(tmp_path):
    output = tmp_path / 'packages'
    result = subprocess.run([sys.executable, str(deb.ROOT / 'packaging/build_deb.py'),
                             '--bundle', str(tmp_path / 'unused-bundle'), '--arch', 'amd64',
                             '--version', '1.0.7', '--output-dir', str(output),
                             '--minimum-glibc', '2.31\nDepends: injected'],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert '最低 glibc' in result.stderr
    assert 'Traceback' not in result.stderr
    assert not output.exists()


@pytest.mark.parametrize('use_cli', [False, True])
@pytest.mark.parametrize('minimum_glibc', [None, '2.39'])
def test_package_dependencies_and_bundled_libraries(tmp_path, use_cli, minimum_glibc):
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    bundle = make_bundle(tmp_path / 'bundle', Path('/bin/true'))
    internal = bundle / '_internal'
    nested = internal / 'PySide6/Qt/lib'
    nested.mkdir(parents=True)
    (nested / 'libxkbcommon.so.0.0.0').write_bytes(b'nested-core')
    (nested / 'libxkbcommon.so.0').symlink_to('libxkbcommon.so.0.0.0')
    (internal / 'libxkbcommon.so').symlink_to('libxkbcommon.so.0')
    (nested / 'libxkbcommon-x11.so.0').symlink_to('missing-system-library')
    retained = ['libQt6Core.so.6', 'libQt6WaylandClient.so.6', 'libwayland-client.so.0',
                'libwayland-cursor.so.0', 'libwayland-egl.so.1', 'libxkbcommon-extra.so.0']
    for name in retained:
        (nested / name).write_bytes(name.encode())
    (internal / 'libQt6Core.so.6').symlink_to('PySide6/Qt/lib/libQt6Core.so.6')
    output = tmp_path / 'packages'
    if use_cli:
        command = [sys.executable, str(deb.ROOT / 'packaging/build_deb.py'),
                   '--bundle', str(bundle), '--arch', architecture, '--version', '1.0.7',
                   '--output-dir', str(output)]
        if minimum_glibc is not None:
            command += ['--minimum-glibc', minimum_glibc]
        subprocess.run(command, check=True, capture_output=True, text=True)
        package = output / f'testtool_1.0.7-1_{architecture}.deb'
    else:
        options = {} if minimum_glibc is None else {'minimum_glibc': minimum_glibc}
        package = deb.build_deb(bundle, architecture, '1.0.7', output, **options)
    dependencies = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Depends'],
                                           text=True).strip().split(', ')
    assert dependencies[0] == 'libc6 (>= ' + (minimum_glibc or '2.31') + ')'
    assert sum(item.startswith('libc6') for item in dependencies) == 1
    for name in ['libxkbcommon0', 'libxkbcommon-x11-0', 'libwayland-client0',
                 'libwayland-cursor0', 'libwayland-egl1', 'xkb-data']:
        assert name in dependencies
    extracted = tmp_path / 'extracted'
    subprocess.run(['dpkg-deb', '-x', str(package), str(extracted)], check=True)
    installed = extracted / 'usr/lib/testtool/_internal'
    assert sorted(path.name for path in installed.rglob('libxkbcommon*.so*')) == ['libxkbcommon-extra.so.0']
    for name in retained:
        assert (installed / 'PySide6/Qt/lib' / name).read_bytes() == name.encode()
    assert (installed / 'libQt6Core.so.6').is_symlink()
    assert (extracted / 'usr/bin/testtool').read_text() == deb.LAUNCHER
    # 原始目录包仍可用于生成独立 tar 包，清理仅作用于 deb 暂存目录。
    assert (internal / 'libxkbcommon.so.0').read_bytes() == b'bundled-core'
    assert (nested / 'libxkbcommon-x11.so.0').is_symlink()


def test_install_upgrade_remove_keeps_user_database(tmp_path):
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    old = deb.build_deb(make_bundle(tmp_path / 'old', Path('/bin/true')), architecture, '1.0.5', tmp_path / 'packages')
    new = deb.build_deb(make_bundle(tmp_path / 'new', Path('/bin/echo')), architecture, '1.0.6', tmp_path / 'packages', minimum_glibc='2.39')
    root = tmp_path / 'root'
    database = root / 'home/tester/.config/TestTool/testtool.db'
    database.parent.mkdir(parents=True)
    database.write_bytes(b'user-history-and-config')
    command = ['fakeroot', 'sh', '-c',
               'dpkg --root="$1" --log="$1/dpkg.log" --force-depends --install "$2" && '
               'dpkg --root="$1" --log="$1/dpkg.log" --force-depends --install "$3"',
               'sh', str(root), str(old), str(new)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert (root / 'usr/lib/testtool/TestTool').read_bytes() == Path('/bin/echo').read_bytes()
    assert not list((root / 'usr/lib/testtool').rglob('libxkbcommon*.so*'))
    version = subprocess.check_output(['dpkg-query', '--admindir=' + str(root / 'var/lib/dpkg'),
                                      '-W', '-f=${Version}', 'testtool'], text=True)
    assert version == '1.0.6-1'
    assert database.read_bytes() == b'user-history-and-config'
    subprocess.run(['fakeroot', 'dpkg', '--root=' + str(root), '--log=' + str(root / 'dpkg.log'), '--purge', 'testtool'],
                   check=True, capture_output=True, text=True)
    assert database.read_bytes() == b'user-history-and-config'
    assert not (root / 'usr/lib/testtool/TestTool').exists()
