"""安装版用户目录与旧 SQLite 数据迁移检查。"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import main


def test_frozen_database_migration_and_upgrade(tmp_path):
    home, install = tmp_path / 'user with spaces', tmp_path / 'install'
    install.mkdir()
    legacy = install / 'testtool.db'
    with sqlite3.connect(str(legacy)) as connection:
        connection.execute('CREATE TABLE history(value TEXT)')
        connection.execute("INSERT INTO history VALUES ('preserved')")
    with patch.object(main.sys, 'frozen', True, create=True), patch.object(main.sys, 'executable', str(install / 'TestTool')), patch.object(Path, 'home', return_value=home):
        destination = Path(main._default_db_path())
        assert destination == home / '.config/TestTool/testtool.db'
        with sqlite3.connect(str(destination)) as connection:
            assert connection.execute('SELECT value FROM history').fetchone()[0] == 'preserved'
            connection.execute("INSERT INTO history VALUES ('new data')")
        assert Path(main._default_db_path()) == destination
        with sqlite3.connect(str(destination)) as connection:
            assert connection.execute('SELECT count(*) FROM history').fetchone()[0] == 2
        assert legacy.exists()


def test_explicit_database_is_preserved(tmp_path):
    with patch.object(main.sys, 'argv', ['TestTool', '--db', str(tmp_path / 'custom.db')]), patch.object(main, 'run_gui') as run, patch.object(main, '_default_db_path') as default:
        main.main()
        run.assert_called_once_with(str(tmp_path / 'custom.db'))
        default.assert_not_called()


def test_qt_settings_migrate_into_user_config_directory(tmp_path):
    settings_type = main.QSettings
    original_format = settings_type.defaultFormat()
    native = settings_type(settings_type.NativeFormat, settings_type.UserScope, 'TestTool', 'TestTool')
    old_native_root = str(Path(native.fileName()).parent.parent)
    old_ini_root = str(Path(settings_type(settings_type.IniFormat, settings_type.UserScope, 'TestTool', 'TestTool').fileName()).parent.parent)
    settings_type.setPath(settings_type.NativeFormat, settings_type.UserScope, str(tmp_path / 'legacy'))
    legacy = settings_type(settings_type.NativeFormat, settings_type.UserScope, 'TestTool', 'TestTool')
    legacy.setValue('columns', 'saved-widths')
    legacy.sync()
    try:
        with patch.object(main.sys, 'frozen', True, create=True), patch.object(Path, 'home', return_value=tmp_path / 'user'):
            main._configure_user_settings()
            current = settings_type(settings_type.defaultFormat(), settings_type.UserScope, 'TestTool', 'TestTool')
            assert Path(current.fileName()) == tmp_path / 'user/.config/TestTool/TestTool.ini'
            assert current.value('columns') == 'saved-widths'
            current.setValue('columns', 'updated-widths')
            current.sync()
            main._configure_user_settings()
            assert settings_type(settings_type.defaultFormat(), settings_type.UserScope, 'TestTool', 'TestTool').value('columns') == 'updated-widths'
    finally:
        settings_type.setDefaultFormat(original_format)
        settings_type.setPath(settings_type.NativeFormat, settings_type.UserScope, old_native_root)
        settings_type.setPath(settings_type.IniFormat, settings_type.UserScope, old_ini_root)
