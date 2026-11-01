"""GUI-only settings stay separate from machine configuration."""
import json
import tempfile
import unittest
from pathlib import Path

import client
import gui_settings


class GuiSettings(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
