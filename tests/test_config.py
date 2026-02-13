import os
import tempfile
import unittest
from pathlib import Path

from photohub.config import (
    compute_app_data_dir_from_root,
    load_settings,
    resolve_app_paths,
    save_settings,
)


class ConfigSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_appdata = os.environ.get('APPDATA')
        os.environ['APPDATA'] = self.tempdir.name

    def tearDown(self):
        if self.old_appdata is None:
            os.environ.pop('APPDATA', None)
        else:
            os.environ['APPDATA'] = self.old_appdata
        self.tempdir.cleanup()

    def test_load_settings_returns_defaults_when_missing(self):
        settings = load_settings()
        self.assertEqual(settings['last_migration_status'], 'idle')
        self.assertIsNone(settings['last_migration_error'])
        self.assertTrue(Path(settings['active_data_dir']).name.lower() == 'photohub')

    def test_save_and_resolve_app_paths_prefers_active_data_dir(self):
        custom_root = Path(self.tempdir.name) / 'my_storage'
        custom_data_dir = compute_app_data_dir_from_root(custom_root)
        save_settings(
            {
                'storage_root': str(custom_data_dir),
                'active_data_dir': str(custom_data_dir),
                'last_migration_status': 'completed',
                'last_migration_error': None,
            }
        )
        paths = resolve_app_paths()
        self.assertEqual(paths.data_dir, custom_data_dir)
        self.assertEqual(paths.projects_dir, custom_data_dir / 'projects')
        self.assertTrue(paths.projects_dir.exists())


if __name__ == '__main__':
    unittest.main()
