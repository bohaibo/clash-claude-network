"""Node-name region inference tests; names are fixtures, not live subscriptions."""
import unittest

import node_region


class NodeRegion(unittest.TestCase):
    def test_iso_tokens_and_prefixes(self):
        self.assertEqual(node_region.infer('demo-sg-01')['code'], 'SG')
        self.assertEqual(node_region.infer('US-01')['code'], 'US')
        self.assertEqual(node_region.infer('jp01')['code'], 'JP')
        self.assertEqual(node_region.infer('HK 02')['code'], 'HK')

    def test_flags_and_country_names(self):
        self.assertEqual(node_region.infer('🇸🇬 Singapore 01')['code'], 'SG')
        self.assertEqual(node_region.infer('🇯🇵 日本 IEPL')['code'], 'JP')
        self.assertEqual(node_region.infer('美国 洛杉矶')['code'], 'US')
        self.assertEqual(node_region.infer('Germany Frankfurt')['code'], 'DE')
        self.assertEqual(node_region.infer('demo-US')['code'], 'US')
        self.assertEqual(node_region.infer('demo-SG')['code'], 'SG')

    def test_unknown_and_ambiguous_names_do_not_guess(self):
        self.assertIsNone(node_region.infer('premium relay 01'))
        self.assertIsNone(node_region.infer('🇺🇸 US-SG relay'))
        self.assertIsNone(node_region.infer(''))
        self.assertIsNone(node_region.infer(None))


if __name__ == '__main__':
    unittest.main()
