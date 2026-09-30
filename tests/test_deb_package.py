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

spec = importlib.util.spec_from_file_location(
    'testtool_deb', Path(__file__).resolve().parents[1] / 'packaging/build_deb.py')
deb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deb)


def make_launcher(tmp_path):
    mock = tmp_path / 'mock app'
    mock.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                    'open(os.environ["TEST_ARGS"],"w").write(json.dumps(sys.argv[1:]))\n')
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
    subprocess.run([str(launcher)], env=environment, check=True)
    assert json.loads(Path(environment['TEST_ARGS']).read_text())[1] == str(tmp_path / 'home/.config/TestTool/testtool.db')
    shutil.rmtree(tmp_path / 'home')
    subprocess.run([str(launcher), '--help'], env=environment, check=True)
    assert not (tmp_path / 'home').exists()
    assert json.loads(Path(environment['TEST_ARGS']).read_text()) == ['--help']


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
    return path


def test_single_file_bundle_rejected(tmp_path):
    try:
        deb.build_deb(Path('/bin/true'), 'amd64', '1.0.6', tmp_path)
    except ValueError:
        return
    raise AssertionError('单文件包未被拒绝')


def test_install_upgrade_remove_keeps_user_database(tmp_path):
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    old = deb.build_deb(make_bundle(tmp_path / 'old', Path('/bin/true')), architecture, '1.0.5', tmp_path / 'packages')
    new = deb.build_deb(make_bundle(tmp_path / 'new', Path('/bin/echo')), architecture, '1.0.6', tmp_path / 'packages')
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
    version = subprocess.check_output(['dpkg-query', '--admindir=' + str(root / 'var/lib/dpkg'),
                                      '-W', '-f=${Version}', 'testtool'], text=True)
    assert version == '1.0.6-1'
    assert database.read_bytes() == b'user-history-and-config'
    subprocess.run(['fakeroot', 'dpkg', '--root=' + str(root), '--log=' + str(root / 'dpkg.log'), '--purge', 'testtool'],
                   check=True, capture_output=True, text=True)
    assert database.read_bytes() == b'user-history-and-config'
    assert not (root / 'usr/lib/testtool/TestTool').exists()
