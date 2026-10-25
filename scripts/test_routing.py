"""Offline routing regressions; synthetic sources and artifacts stay under .local/."""
import copy,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import client,test_snapshots
from routing_checks import audit_ingress,audit_lan_provider,lan_domain,normalize_lan_cidrs

class Routing(unittest.TestCase):
 def setUp(self):
  test_snapshots.Snapshots.setUp(self)
  self.policy=json.loads((client.PROJECT/'assets/claude-policy.json').read_text())
  self.data=dict(self.policy,domains=['+.cn'],ips=['1.0.1.0/24'],privateDomains=['+.lan'],lanCidrs=['10.0.0.0/8'],lan=[])
  self.base={'proxies':[{'name':'Chosen','type':'socks5','server':'example.invalid','port':1234}],
             'proxy-groups':[],'rule-providers':{},'rules':['MATCH,DIRECT']}
 def render(self,original=None,data=None):
  original=self.base if original is None else original;data=self.data if data is None else data
  script=(client.PROJECT/'assets/secure-main.js').read_text()
  code=script+'\nprocess.stdout.write(JSON.stringify(secureMain('+json.dumps(original)+','+json.dumps(data)+',"Chosen",'+json.dumps(client.GROUP)+')));'
  run=subprocess.run(['node','-e',code],capture_output=True,text=True,encoding='utf-8')
  if run.returncode:raise AssertionError('Native renderer rejected additional ingress')
  return json.loads(run.stdout)
 def test_actual_lan_and_reviewed_names_are_preserved(self):
  payload=['IP-CIDR,192.168.1.0/24,no-resolve','IP-CIDR6,fd00::/8,no-resolve','DOMAIN-SUFFIX,lan','DOMAIN,router.asus.com']
  provider={'type':'inline','behavior':'classical','payload':payload}
  self.assertEqual(audit_lan_provider(provider,self.policy),provider)
 def test_public_ranges_are_not_treated_as_lan(self):
  audit=[]
  result=normalize_lan_cidrs(['192.168.1.0/24','192.0.2.0/24','192.88.99.0/24','224.0.0.0/3'],audit)
  self.assertIn('192.168.1.0/24',result);self.assertNotIn('192.88.99.0/24',result)
  self.assertIn('224.0.0.0/4',result);self.assertTrue(audit)
 def test_whole_network_lan_rule_is_rejected(self):
  for cidr in ('0.0.0.0/0','::/0'):
   with self.subTest(cidr=cidr),self.assertRaises(AssertionError):normalize_lan_cidrs([cidr],[])
 def test_protected_or_unreviewed_domains_cannot_enter_lan(self):
  for domain in ('+.claude.ai','+.ai','local.claude.ai','+.anthropic.com','+.sentry.io','+.evil.example.com','*'):
   with self.subTest(domain=domain),self.assertRaises(AssertionError):lan_domain(domain,self.policy)
 def test_reverse_local_zones_are_narrow(self):
  for domain in ('+.168.192.in-addr.arpa','+.64.100.in-addr.arpa','+.d.f.ip6.arpa'):lan_domain(domain,self.policy)
  for domain in ('+.in-addr.arpa','+.8.8.in-addr.arpa','+.ip6.arpa'):
   with self.subTest(domain=domain),self.assertRaises(AssertionError):lan_domain(domain,self.policy)
 def test_classical_match_keyword_and_logical_rules_rejected(self):
  for item in ('MATCH','DOMAIN-KEYWORD,claude','AND,((DOMAIN,claude.ai))','PROCESS-NAME,chrome.exe'):
   with self.subTest(item=item),self.assertRaises(AssertionError):
    audit_lan_provider({'type':'inline','behavior':'classical','payload':[item]},self.policy)
 def test_lan_provider_cannot_shadow_protected_domain(self):
  original=copy.deepcopy(self.base)
  original['rule-providers']['office-private']={'type':'inline','behavior':'classical','payload':['DOMAIN-SUFFIX,claude.ai']}
  config=self.render(original,dict(self.data,lan=['RULE-SET,office-private,DIRECT']))
  with self.assertRaises(AssertionError):client.validate(config,original,'Chosen')
 def test_private_domain_source_cannot_shadow_protected_domain(self):
  config=self.render(data=dict(self.data,privateDomains=['+.claude.ai']))
  with self.assertRaises(AssertionError):client.validate(config,self.base,'Chosen')
 def test_unfrozen_early_direct_provider_rejected(self):
  original=copy.deepcopy(self.base);original['rule-providers']['office-private']={'type':'http','behavior':'domain','url':'https://example.invalid/lan'}
  config=self.render(original,dict(self.data,lan=['RULE-SET,office-private,DIRECT']))
  with self.assertRaises(AssertionError):client.validate(config,original,'Chosen')
 def test_extra_direct_listener_rejected_in_renderer_and_validator(self):
  entry={'name':'audit','type':'http','listen':'127.0.0.1','port':19080,'proxy':'DIRECT'}
  with self.assertRaises(AssertionError):self.render(dict(self.base,listeners=[entry]))
  config=self.render();config['listeners']=[entry]
  with self.assertRaises(AssertionError):client.validate(config,self.base,'Chosen')
 def test_extra_direct_tunnel_rejected_in_both_formats(self):
  for tunnel in ('tcp,127.0.0.1:19080,example.invalid:443,DIRECT',
                 {'network':['tcp'],'address':'127.0.0.1:19080','target':'example.invalid:443','proxy':'DIRECT'}):
   with self.subTest(tunnel_type=type(tunnel).__name__):
    with self.assertRaises(AssertionError):self.render(dict(self.base,tunnels=[tunnel]))
    with self.assertRaises(AssertionError):audit_ingress({'tunnels':[tunnel]},client.GROUP)
 def test_reviewed_loopback_pinned_ingress_is_preserved(self):
  original=dict(self.base,listeners=[{'name':'audit','type':'http','listen':'127.0.0.1','port':19080,'proxy':client.GROUP}],
                tunnels=['tcp/udp,[::1]:19081,example.invalid:443,'+client.GROUP])
  config=self.render(original);client.validate(config,original,'Chosen')
  self.assertEqual(config['listeners'],original['listeners']);self.assertEqual(config['tunnels'],original['tunnels'])
 def test_unknown_or_public_additional_ingress_stops(self):
  for entry in ({'type':'http','listen':'0.0.0.0','proxy':client.GROUP},
                {'type':'http','listen':'127.0.0.1'},
                {'type':'http','listen':'127.0.0.1','proxy':client.GROUP,'rule':'other'},
                {'type':'tun','listen':'127.0.0.1','proxy':client.GROUP}):
   with self.subTest(entry=entry):
    with self.assertRaises(AssertionError):self.render(dict(self.base,listeners=[entry]))
    with self.assertRaises(AssertionError):audit_ingress({'listeners':[entry]},client.GROUP)
 def test_validator_rejects_direct_and_global_modes(self):
  for mode in ('direct','global',None):
   config=self.render();config['mode']=mode
   with self.subTest(mode=mode),self.assertRaises(AssertionError):client.validate(config,self.base,'Chosen')
 def source(self,provider):
  (self.root/'profiles').mkdir()
  original=copy.deepcopy(self.base);original['rule-providers']['office-private']=provider
  original['rules']=['RULE-SET,office-private,DIRECT','MATCH,DIRECT']
  path=self.root/'profiles/source.yaml';path.write_text(client.yaml.safe_dump(original))
  index={'items':[{'uid':'a','type':'remote','file':'source.yaml'}]}
  (self.root/'profiles.yaml').write_text(client.yaml.safe_dump(index))
  return index['items'][0],path
 def test_build_freezes_safe_lan_and_preserves_source(self):
  item,path=self.source({'type':'inline','behavior':'classical','payload':['IP-CIDR,192.168.1.0/24,no-resolve','DOMAIN-SUFFIX,lan']})
  before=path.read_bytes();real_run=subprocess.run
  def run(args,**kwargs):
   if args[0]=='fixture-core':return subprocess.CompletedProcess(args,0,b'',b'')
   return real_run(args,**kwargs)
  with patch.object(client,'executables',return_value=('node','fixture-core')),patch.object(client.subprocess,'run',side_effect=run):
   _,_,candidate,_=client.build(self.root,item,'Chosen',self.data)
  self.assertEqual(path.read_bytes(),before)
  self.assertEqual(candidate['rule-providers']['office-private']['payload'],['IP-CIDR,192.168.1.0/24,no-resolve','DOMAIN-SUFFIX,lan'])
 def test_build_rejects_unsafe_provider_before_publication(self):
  item,path=self.source({'type':'inline','behavior':'classical','payload':['DOMAIN-SUFFIX,claude.ai']})
  before=path.read_bytes()
  with self.assertRaises(AssertionError):client.build(self.root,item,'Chosen',self.data)
  self.assertEqual(path.read_bytes(),before);self.assertFalse((self.local/'candidate.yaml').exists())

if __name__=='__main__':unittest.main()
