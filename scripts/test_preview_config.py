import json, tempfile, unittest
from pathlib import Path
import preview_config
class Preview(unittest.TestCase):
 def test_fixed_node_comes_from_single_project_group_only(self):
  from client import GROUP
  for nodes, expected in [(['demo-sg'], 'demo-sg'), (['DIRECT'], None), (['a', 'b'], None), ([], None)]:
   self.assertEqual(preview_config.network({'proxy-groups':[{'name':GROUP,'proxies':nodes}]})['fixed_node'],expected)
  self.assertIsNone(preview_config.network({'proxy-groups':[{'name':'other','proxies':['demo-sg']}]})['fixed_node'])
 def test_dns_credentials_and_url_tokens_are_hidden(self):
  v=preview_config.network({'dns':{'nameserver':['https://user:secret@resolver.invalid/token?key=hidden#group']}})
  text=json.dumps(v)
  self.assertIn('resolver.invalid',text)
  for secret in ('user','secret','token','hidden'):
   self.assertNotIn(secret,text)
 def test_disabled_tun_is_distinct_from_missing(self):
  self.assertIn('关闭',preview_config.network({'tun':{'enable':False}})['tun'])
  self.assertIn('未显式设置',preview_config.network({})['tun'])
 def test_proxy_values_are_hidden_and_missing_file_is_clear(self):
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder)/'.npmrc'
   self.assertIn('不存在',preview_config.app_proxy('npm',p))
   p.write_text('https-proxy=https://user:secret@private.invalid\n')
   result=preview_config.app_proxy('npm',p)
   self.assertIn('https-proxy',result)
   self.assertNotIn('secret',result)
if __name__=='__main__':unittest.main()
