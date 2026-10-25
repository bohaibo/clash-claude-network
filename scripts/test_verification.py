"""Offline verification regressions. All network/process I/O is mocked."""
import contextlib,io,json,os,socket,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import client,verify,verify_core_exit as core,verification_reports

class Verification(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='verification-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.broken=False
  self.enterContext(patch.dict(os.environ));os.environ.pop('CCN_VERIFY_RUN',None)
  for module in (verify,verification_reports):self.enterContext(patch.object(module,'LOCAL',self.local))
  (self.local/'plan.json').write_text(json.dumps({'root':str(self.local),'node':'Chosen','region':'SG'}))
  (self.local/'clash-verge.yaml').write_text(json.dumps({'proxies':[{'name':'Chosen','port':1234}]}))
  self.enterContext(patch.object(verify,'root_path',return_value=self.local))
  self.api=self.enterContext(patch.object(verify,'api',side_effect=self.fake_api))
  self.enterContext(patch.object(verify,'curl',side_effect=self.fake_curl))
  self.enterContext(patch.object(verify,'query',return_value={'passed':True,'ips':['198.18.0.1']}))
  self.stun=self.enterContext(patch.object(verify,'stun',return_value={'ok':True,'mapped':'8.8.8.8:1234','route':{'verified':True}}))
  self.git=self.enterContext(patch.object(verify,'git_egress',return_value={'tested':True,'ok':True}))
  self.npm=self.enterContext(patch.object(verify,'npm_egress',return_value={'tested':True,'ok':True}))
  self.enterContext(patch.object(verify,'workload_scope',return_value={'docker_installed':False,'wsl_installed':False}))
  self.enterContext(contextlib.redirect_stdout(io.StringIO()))
 def fake_api(self,root,method,path,body=None):
  if method=='PUT':self.broken=client.yaml.safe_load(body['payload'])['proxies'][0]['port']==1;return {}
  if path=='/configs':return {'mode':'rule'}
  if path=='/connections':return {'connections':[]}
  return {'proxies':{client.GROUP:{'all':['Chosen'],'now':'Chosen'}}}
 def fake_curl(self,url,head=False):
  if '[' in url or (self.broken and url=='https://claude.ai'):return {'exit':7,'http':0,'body':''}
  return {'exit':0,'http':200,'body':json.dumps({'country':'SG','ip':'8.8.8.8'})}
 def test_failed_required_checks_cannot_pass(self):
  for name in ('stun','git','npm'):
   with self.subTest(check=name):
    mock=getattr(self,name);old=mock.return_value;mock.return_value={'tested':True,'ok':False}
    try:
     with self.assertRaisesRegex(AssertionError,name):verify.verify()
     report=json.loads((self.local/'verification.json').read_text())
     self.assertFalse(report['success']);self.assertEqual(report[name]['status'],'failed')
    finally:mock.return_value=old
 def test_missing_tools_are_explicitly_untested(self):
  self.git.return_value={'tested':False,'reason':'git not installed'}
  report=verify.verify();self.assertTrue(report['success'])
  self.assertIn('git',report['not_tested']);self.assertEqual(report['git']['status'],'not-tested')
 def test_operation_keeps_outage_core_and_recovery_evidence(self):
  os.environ['CCN_VERIFY_RUN']='a'*32
  verify.verify(True)
  verification_reports.save_report({'success':True,'domestic_also_blocked':True},'core-exit')
  verify.verify(stage='post-recovery')
  report=json.loads((self.local/'verification.json').read_text())
  self.assertTrue(report['success']);self.assertEqual(set(report['stages']),{'node-outage','core-exit','post-recovery'})
  self.assertTrue(report['stages']['node-outage']['node_failure_blocks_foreign_preserves_domestic'])
  self.assertTrue(report['stages']['node-outage']['recovered'])
  self.assertTrue(report['stages']['core-exit']['domestic_also_blocked'])
  self.assertEqual(len(list((self.local/'verification-runs'/('a'*32)).glob('*.json'))),3)
 def test_later_success_does_not_hide_same_operation_failure(self):
  os.environ['CCN_VERIFY_RUN']='b'*32
  verification_reports.save_report({'success':False},'core-exit')
  verify.verify(stage='post-recovery')
  self.assertFalse(json.loads((self.local/'verification.json').read_text())['success'])
 def test_new_operation_does_not_inherit_old_success(self):
  os.environ['CCN_VERIFY_RUN']='c'*32;verify.verify(True)
  os.environ['CCN_VERIFY_RUN']='d'*32;verify.verify()
  report=json.loads((self.local/'verification.json').read_text())
  self.assertEqual(set(report['stages']),{'connectivity'})
  self.assertTrue((self.local/'verification-runs'/('c'*32)/'node-outage.json').exists())
 def test_invalid_operation_id_cannot_escape_local(self):
  os.environ['CCN_VERIFY_RUN']='../outside'
  with self.assertRaises(ValueError):verification_reports.save_report({'success':True},'connectivity')
 def test_outage_failure_still_restores_node_and_keeps_failed_evidence(self):
  with patch.object(verify,'curl',side_effect=lambda url,head=False: {'exit':0,'http':200,'body':json.dumps({'country':'SG'})} if self.broken else self.fake_curl(url,head)):
   with self.assertRaises(AssertionError):verify.verify(True)
  self.assertFalse(self.broken)
  report=json.loads((self.local/'verification.json').read_text())
  self.assertFalse(report['success']);self.assertIn('node-outage',report['stages'])
 def test_live_direct_mode_stops_before_network_probes(self):
  for mode in ('direct','global',None):
   with self.subTest(mode=mode),patch.object(verify,'api',return_value={'mode':mode}),patch.object(verify,'curl') as curl:
    with self.assertRaisesRegex(AssertionError,'rule mode'):verify.verify()
    curl.assert_not_called()
 def test_udp_route_missing_cannot_pass(self):
  self.stun.return_value.pop('route')
  with self.assertRaisesRegex(AssertionError,'UDP exit'):verify.verify()
 def test_udp_country_mismatch_fails(self):
  self.stun.return_value['mapped']='8.8.4.4:1234'
  def lookup(url,head=False):
   if url=='https://ipinfo.io/8.8.4.4/json':return {'exit':0,'http':200,'body':json.dumps({'ip':'8.8.4.4','country':'CN'})}
   return self.fake_curl(url,head)
  with patch.object(verify,'curl',side_effect=lookup):
   with self.assertRaisesRegex(AssertionError,'UDP exit'):verify.verify()
  report=json.loads((self.local/'verification.json').read_text());self.assertFalse(report['udp_egress']['country_matches'])
 def test_different_udp_exit_in_required_region_can_pass(self):
  self.stun.return_value['mapped']='8.8.4.4:1234'
  def lookup(url,head=False):
   if url=='https://ipinfo.io/8.8.4.4/json':return {'exit':0,'http':200,'body':json.dumps({'ip':'8.8.4.4','country':'SG'})}
   return self.fake_curl(url,head)
  with patch.object(verify,'curl',side_effect=lookup):report=verify.verify()
  self.assertTrue(report['udp_egress']['verified']);self.assertFalse(report['udp_egress']['same_as_tcp'])
 def test_unavailable_udp_country_is_not_success(self):
  self.stun.return_value['mapped']='8.8.4.4:1234'
  with patch.object(verify,'curl',side_effect=self.fake_curl):
   with self.assertRaisesRegex(AssertionError,'UDP exit'):verify.verify()

class UdpRoute(unittest.TestCase):
 def probe(self,connections):
  with patch.object(verify,'api',return_value={'connections':connections}):
   return verify.udp_route(None,'Chosen',{'host':'stun.example.invalid','source_port':45678,'destination_port':19302,'destination_ip':'198.18.0.2'})
 def connection(self,chains=None,**metadata):
  return {'metadata':dict(network='udp',sourcePort='45678',destinationPort='19302',host='stun.example.invalid',**metadata),
          'chains':['Chosen',client.GROUP] if chains is None else chains}
 def test_pinned_udp_connection_passes(self):self.assertTrue(self.probe([self.connection()])['verified'])
 def test_direct_or_different_node_fails(self):
  for chains in (['DIRECT'],['Other',client.GROUP],['Chosen','DIRECT',client.GROUP]):
   with self.subTest(chains=chains):self.assertFalse(self.probe([self.connection(chains)])['verified'])
 def test_missing_ambiguous_or_wrong_socket_fails(self):
  wrong=self.connection();wrong['metadata']['sourcePort']='45679'
  for connections in ([],[wrong],[self.connection(),self.connection()]):
   with self.subTest(count=len(connections)):self.assertFalse(self.probe(connections)['verified'])

class CoreExit(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='core-verification-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name)
  self.enterContext(patch.dict(os.environ));os.environ['CCN_VERIFY_RUN']='e'*32
  for module in (core,verification_reports):self.enterContext(patch.object(module,'LOCAL',self.local))
  (self.local/'plan.json').write_text(json.dumps({'root':str(self.local)}))
  (self.local/'clash-verge.yaml').write_text('{}')
  self.enterContext(patch.object(core,'root_path',return_value=self.local))
  self.commands=[]
  def command(text):self.commands.append(text);return '0'
  self.command=self.enterContext(patch.object(core,'command',side_effect=command))
  self.api=self.enterContext(patch.object(core,'api',return_value={}))
  self.connection=self.enterContext(patch.object(core.socket,'create_connection',side_effect=OSError('fixture blocked')))
  self.enterContext(contextlib.redirect_stdout(io.StringIO()))
 def saved(self):return json.loads((self.local/'verification.json').read_text())['stages']['core-exit']
 def test_core_outage_and_recovery_are_recorded(self):
  report=core.verify_core_exit();self.assertTrue(report['success']);self.assertTrue(self.saved()['domestic_also_blocked'])
  self.assertTrue(any(text.startswith('Start-Service') for text in self.commands));self.api.assert_called_once()
 def test_leak_failure_still_restarts_core_and_records_failure(self):
  self.connection.side_effect=None;self.connection.return_value=contextlib.nullcontext()
  self.assertFalse(core.verify_core_exit()['success']);self.assertFalse(self.saved()['success'])
  self.assertTrue(any(text.startswith('Start-Service') for text in self.commands))
 def test_restart_failure_still_saves_stage_evidence(self):
  def command(text):
   if text.startswith('Start-Service'):raise RuntimeError('fixture')
   return '0'
  self.command.side_effect=command
  self.assertFalse(core.verify_core_exit()['success']);self.assertTrue(self.saved()['restart_failed'])
 def test_recovery_timeout_is_failed_evidence(self):
  self.api.side_effect=RuntimeError('fixture unavailable')
  self.assertFalse(core.verify_core_exit(recovery_timeout=0)['success']);self.assertTrue(self.saved()['restart_failed'])

class Stun(unittest.TestCase):
 def probe(self,transform=lambda data,request:data,sender=('192.0.2.1',19302),attribute=None,route_probe=None):
  # Synthetic server replies to a mocked socket, never sends a real datagram.
  case=self
  if attribute is None:
   address=bytes(a^b for a,b in zip(socket.inet_aton('8.8.8.8'),struct.pack('!I',0x2112A442)))
   attribute=struct.pack('!HHBBH',0x0020,8,0,1,1234^0x2112)+address
  class FakeSocket:
   def __enter__(self):case.socket_open=True;return self
   def __exit__(self,*args):case.socket_open=False
   def getsockname(self):return ('0.0.0.0',45678)
   def settimeout(self,value):pass
   def sendto(self,request,target):
    case.assertEqual(struct.unpack('!HHI',request[:8]),(1,0,0x2112A442));self.request=request
   def recvfrom(self,size):
    data=struct.pack('!HHI',0x0101,len(attribute),0x2112A442)+self.request[8:20]+attribute
    return transform(data,self.request),sender
  with patch.object(verify.socket,'socket',return_value=FakeSocket()),patch.object(verify.socket,'gethostbyname',return_value='192.0.2.1'):
   return verify.stun(route_probe=route_probe)
 def test_route_sample_collected_before_udp_socket_closes(self):
  def capture(probe):
   self.assertTrue(self.socket_open);self.assertEqual(probe['source_port'],45678)
   return {'verified':True}
  self.assertTrue(self.probe(route_probe=capture)['route']['verified']);self.assertFalse(self.socket_open)
 def test_valid_xor_mapping(self):self.assertEqual(self.probe(),{'ok':True,'mapped':'8.8.8.8:1234'})
 def test_valid_plain_mapping(self):
  attr=struct.pack('!HHBBH',1,8,0,1,4321)+socket.inet_aton('8.8.4.4')
  self.assertEqual(self.probe(attribute=attr),{'ok':True,'mapped':'8.8.4.4:4321'})
 def test_wrong_transaction_rejected(self):self.assertFalse(self.probe(lambda data,request:data[:8]+b'X'*12+data[20:])['ok'])
 def test_wrong_cookie_rejected(self):self.assertFalse(self.probe(lambda data,request:data[:4]+b'\0'*4+data[8:])['ok'])
 def test_wrong_sender_rejected(self):self.assertFalse(self.probe(sender=('192.0.2.2',19302))['ok'])
 def test_missing_mapping_rejected(self):self.assertFalse(self.probe(attribute=b'')['ok'])
 def test_truncated_message_rejected(self):self.assertFalse(self.probe(lambda data,request:data[:-1])['ok'])
 def test_truncated_attribute_rejected(self):self.assertFalse(self.probe(attribute=struct.pack('!HH',0x0020,8))['ok'])
 def test_private_mapping_rejected(self):
  self.assertFalse(self.probe(attribute=struct.pack('!HHBBH',1,8,0,1,4321)+socket.inet_aton('192.168.1.1'))['ok'])

if __name__=='__main__':unittest.main()
