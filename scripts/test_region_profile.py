"""Region-profile mapping tests; no system timezone or language is changed."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import region_profile


class RegionProfile(unittest.TestCase):
    def test_singapore_and_united_states(self):
        sg = region_profile.get('sg')
        self.assertEqual(sg['region'], 'SG')
        self.assertEqual(sg['culture'], 'zh-SG')
        self.assertEqual(sg['timezone_id'], 'Singapore Standard Time')
        self.assertEqual(sg['iana'], 'Asia/Singapore')
        us = region_profile.get('US')
        self.assertEqual(us['culture'], 'en-US')
        self.assertEqual(us['timezone_id'], 'Eastern Standard Time')
        self.assertEqual(us['iana'], 'America/New_York')

    def test_every_profile_has_required_fields(self):
        for code, profile in region_profile.PROFILES.items():
            with self.subTest(code=code):
                self.assertRegex(profile['culture'], r'^[A-Za-z]{2,3}(-[A-Za-z0-9]+)+$')
                self.assertEqual(profile['timezone_id'], profile['timezone_id'].strip())
                self.assertRegex(profile['iana'], r'^[A-Za-z_]+(?:/[A-Za-z_]+)+$')

    def test_unknown_region_is_explicitly_not_known(self):
        value = region_profile.get('ZZ')
        self.assertFalse(value['known'])
        self.assertEqual(value['region'], 'ZZ')
        with self.assertRaises(ValueError):
            region_profile.get('USA')

    def test_cli_returns_json(self):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(region_profile.__file__)), '--region', 'JP'],
            capture_output=True, text=True, encoding='utf-8', check=True,
        )
        value = json.loads(result.stdout)
        self.assertEqual(value['iana'], 'Asia/Tokyo')



if __name__ == '__main__':
    unittest.main()
