"""Offline chain direction, exact selection and fail-closed configuration tests."""
import copy, json, tempfile, unittest
from pathlib import Path
import chain_profile, client


class ChainProfile(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='chain-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'profiles').mkdir()
        self.profiles = {'items': [{'uid': 'exit', 'type': 'remote', 'file': 'exit.yaml'},
                                   {'uid': 'front', 'type': 'remote', 'file': 'front.yaml'}]}
        for name in ('exit', 'front'):
            (self.root / 'profiles' / (name + '.yaml')).write_text(json.dumps({'proxies': [
                {'name': name, 'type': 'socks5', 'server': 'example.invalid', 'port': 1234}]}))
        self.record = {'source_uid': 'exit', 'source_file': 'exit.yaml', 'exit_node': 'exit',
                       'front_uid': 'front', 'front_file': 'front.yaml', 'front_node': 'front',
                       'node': 'chain'}
        self.base = {'mode': 'rule', 'rules': ['RULE-SET,strict-cn-domain,DIRECT',
                                             'MATCH,' + chain_profile.GROUP],
                     'rule-providers': {'strict-cn-domain': {'type': 'inline', 'payload': ['+.cn']},
                                        'unused': {'type': 'http', 'url': 'https://example.invalid'}},
                     'secret': 'fixture-only', 'proxies': [{'name': 'unrelated'}]}

    def test_only_exit_has_front_dialer_and_empty_groups_reject(self):
        before = copy.deepcopy(self.base)
        value = chain_profile.render(self.base, self.record, self.root, self.profiles)
        self.assertEqual(self.base, before)
        self.assertEqual(value['proxies'], [])
        for provider in value['proxy-providers'].values():
            self.assertEqual(provider['type'], 'inline')
            self.assertNotIn('path', provider)
            self.assertEqual(len(provider['payload']), 1)
        self.assertEqual(value['proxy-providers']['chain-exit-source']['override']['dialer-proxy'], chain_profile.FRONT)
        self.assertNotIn('override', value['proxy-providers']['chain-front-source'])
        self.assertEqual(value['proxy-providers']['chain-exit-source']['filter'], '^exit$')
        self.assertEqual(value['proxy-providers']['chain-exit-source']['override']['proxy-name'][0]['target'], 'chain')
        for group in value['proxy-groups']:
            self.assertEqual(group['empty-fallback'], 'REJECT')
            self.assertNotIn('proxies', group)
        self.assertNotIn('secret', value)
        self.assertEqual(set(value['rule-providers']), {'strict-cn-domain'})

    def test_missing_exact_source_or_nested_chain_stops(self):
        file = self.root / 'profiles/front.yaml'
        for nodes in [[], [{'name': 'replacement'}], [{'name': 'front', 'dialer-proxy': 'exit'}],
                      [{'name': 'front'}, {'name': 'front'}]]:
            file.write_text(json.dumps({'proxies': nodes}))
            with self.assertRaises(AssertionError):
                chain_profile.render(self.base, self.record, self.root, self.profiles)

    def test_filename_cannot_escape_profile_directory(self):
        for value in ('../escape.yaml', str(self.root / 'escape.yaml'), '..'):
            with self.assertRaises(AssertionError):
                chain_profile.profile_file(self.root, value)

    def test_source_node_dns_is_scoped_and_type_filtered(self):
        source = {'proxies': [{'name': 'front', 'server': 'entry.example.invalid'}],
                  'dns': {'proxy-server-nameserver': ['https://resolver.example.invalid:44443/dns-query#OldGroup&h3=false']}}
        (self.root / 'profiles/front.yaml').write_text(json.dumps(source))
        self.base['dns'] = {'proxy-server-nameserver': ['https://223.5.5.5/dns-query'],
                            'nameserver': ['https://1.1.1.1/dns-query#' + chain_profile.GROUP]}
        before = copy.deepcopy(self.base['dns'])
        value = chain_profile.render(self.base, self.record, self.root, self.profiles)
        self.assertEqual(value['dns']['nameserver'], before['nameserver'])
        self.assertEqual(value['dns']['proxy-server-nameserver'], before['proxy-server-nameserver'])
        self.assertEqual(value['dns']['proxy-server-nameserver-policy'], {
            'entry.example.invalid': ['https://resolver.example.invalid:44443/dns-query#DIRECT&h3=false&disable-qtype-64=true&disable-qtype-65=true']})
        first = chain_profile.source_fingerprint(self.root, self.profiles, self.record)
        source['dns']['proxy-server-nameserver'] = ['https://other.example.invalid/dns-query']
        (self.root / 'profiles/front.yaml').write_text(json.dumps(source))
        self.assertNotEqual(chain_profile.source_fingerprint(self.root, self.profiles, self.record), first)


if __name__ == '__main__':
    unittest.main()
