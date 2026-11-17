"""GUI-only settings stay separate from machine configuration."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_settings
import gui_server


class GuiSettings(unittest.TestCase):
    def test_days_require_an_explicit_integer_before_any_mutation(self):
        gui_settings.save('manual','13:00',7,self.local)
        previous=gui_settings.settings_path(self.local).read_bytes()
        app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
        for days in (True,False,1.0,1.9,float('inf'),float('-inf'),float('nan'),None,[],{},'1.9',' 7 ','７','9'*1000):
            with self.subTest(days=repr(days)):
                with self.assertRaises(ValueError):gui_settings.save('scheduled','13:00',days,self.local)
                with patch.object(app,'settings_operation',side_effect=AssertionError('must validate first')):
                    with self.assertRaises(gui_server.ApiError) as caught:app.save_update_settings({'mode':'scheduled','time':'13:00','days':days})
                self.assertEqual(caught.exception.status,400)
                self.assertEqual(gui_settings.settings_path(self.local).read_bytes(),previous)
        for days in (1,365,'7'):
            self.assertEqual(gui_settings.validate('scheduled','13:00',days)[2],int(days))

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='gui-settings-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)

    def test_defaults_are_scheduled_at_1300(self):
        value = gui_settings.load(self.local)
        self.assertEqual(value['update'], {'mode': 'scheduled', 'time': '13:00', 'days': 1})

    def test_roundtrip_manual_and_scheduled(self):
        gui_settings.save('manual', '08:15', 1, self.local)
        self.assertEqual(gui_settings.update(self.local), {'mode': 'manual', 'time': '08:15', 'days': 1})
        gui_settings.save('scheduled', '23:59', 30, self.local)
        self.assertEqual(gui_settings.update(self.local), {'mode': 'scheduled', 'time': '23:59', 'days': 30})
        stored = json.loads((self.local / 'ui-settings.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 2)

    def test_invalid_values_are_rejected(self):
        for mode, time_value, days in (('auto', '13:00', 1), ('manual', '24:00', 1), ('scheduled', '9:00', 1),
                                       ('manual', '13:60', 1), ('scheduled', '13:00', 0), ('scheduled', '13:00', 366)):
            with self.subTest(mode=mode, time=time_value, days=days):
                with self.assertRaises(ValueError):
                    gui_settings.validate(mode, time_value, days)

    def test_corrupt_file_is_not_overwritten(self):
        path = self.local / 'ui-settings.json'
        path.write_text('{"update":', encoding='utf-8')
        with self.assertRaises(ValueError):
            gui_settings.load(self.local)
        self.assertEqual(path.read_text(encoding='utf-8'), '{"update":')

    def test_failed_flush_preserves_previous_file(self):
        gui_settings.save('manual', '08:15', 1, self.local)
        original = gui_settings.settings_path(self.local).read_bytes()
        with patch.object(gui_settings.os, 'fsync', side_effect=OSError('flush fixture')):
            with self.assertRaises(OSError):
                gui_settings.save('scheduled', '09:00', 7, self.local)
        self.assertEqual(gui_settings.settings_path(self.local).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
