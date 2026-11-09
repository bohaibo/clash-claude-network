import tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch,MagicMock
import client

class RuleDownload(unittest.TestCase):
 def test_direct_tls_failure_uses_only_loopback_proxy(self):
  with tempfile.TemporaryDirectory(dir=client.LOCAL) as folder:
   root=Path(folder);(root/'clash-verge.yaml').write_text('mixed-port: 7890\n')
   response=MagicMock();response.__enter__.return_value.read.return_value=b'fixture'
   opener=MagicMock();opener.open.side_effect=[urllib.error.URLError('TLS EOF'),OSError('TLS EOF'),response]
   with patch.object(client,'root_path',return_value=root),patch.object(client.urllib.request,'build_opener',return_value=opener),patch.object(client.urllib.request,'ProxyHandler',side_effect=lambda routes:routes) as handler:
    self.assertEqual(client.rule_source_bytes(client.SOURCES['domains']),b'fixture')
    self.assertEqual(handler.call_args.args[0],{'https':'http://127.0.0.1:7890'})
    self.assertEqual(opener.open.call_count,3)
 def test_failure_is_bounded_and_actionable(self):
  with patch.object(client,'root_path',side_effect=OSError),patch.object(client.urllib.request,'build_opener') as build:
   build.return_value.open.side_effect=urllib.error.URLError('sensitive detail')
   with self.assertRaisesRegex(RuntimeError,'规则下载失败') as error:client.rule_source_bytes(client.SOURCES['ips'])
   self.assertNotIn('sensitive detail',str(error.exception))
   self.assertEqual(build.return_value.open.call_count,2)
 def test_unknown_source_never_opens_network(self):
  with patch.object(client.urllib.request,'build_opener') as build:
   with self.assertRaises(Exception):client.rule_source_bytes('https://invalid.example/rules')
   build.assert_not_called()
 def test_oversize_is_not_retried(self):
  response=MagicMock();response.__enter__.return_value.read.return_value=b'x'*4_000_001
  with patch.object(client,'root_path',side_effect=OSError),patch.object(client.urllib.request,'build_opener') as build:
   build.return_value.open.return_value=response
   with self.assertRaisesRegex(Exception,'Oversized'):client.rule_source_bytes(client.SOURCES['ips'])
   self.assertEqual(build.return_value.open.call_count,1)
