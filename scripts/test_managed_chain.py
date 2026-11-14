"""Offline lifecycle, routing equivalence and rollback for managed two-hop nodes."""
import copy, json, subprocess, unittest
from pathlib import Path
from unittest.mock import patch
import client, managed_chain, chain_profile, test_snapshots, update, gui_server


class ManagedChain(unittest.TestCase):
    def setUp(self):
        test_snapshots.Snapshots.setUp(self)
        (self.root/'profiles').mkdir()
        self.index={'current':'exit','items':[
            {'uid':'exit','type':'remote','name':'Exit SG','file':'exit.yaml'},
            {'uid':'front','type':'remote','name':'Entry HK','file':'front.yaml'}]}
        self.exit={'proxies':[{'name':'Exit','type':'socks5','server':'192.0.2.10','port':1234}],
                   'proxy-groups':[],'rule-providers':{},'rules':['MATCH,DIRECT']}
        self.front={'proxies':[{'name':'Entry','type':'socks5','server':'entry.example.invalid','port':4321}],
                    'dns':{'proxy-server-nameserver':['https://resolver.example.invalid/dns-query']}}
        for path,value in [('profiles.yaml',self.index),('profiles/exit.yaml',self.exit),('profiles/front.yaml',self.front),
                           ('clash-verge.yaml',{}),('config.yaml',{}),('verge.yaml',{})]:
            self.put(path,value)
        for name in ('geosite.dat','geoip.dat'):(self.root/name).write_bytes(b'offline fixture')
        self.data={'domains':['+.cn'],'ips':['1.0.1.0/24'],'privateDomains':['+.lan'],'lanCidrs':['10.0.0.0/8']}
        self.data['ruleSources']={k:{'url':v,'sha256':'f'*64,'count':1} for k,v in client.SOURCES.items()}
        self.request={'name':'Entry-Exit','front_subscription':'front','front_node':'Entry',
                      'exit_subscription':'exit','exit_node':'Exit'}
        node=str(client.PROJECT/'.runtime/node/node.exe');self.core='offline-core.exe'
        self.enterContext(patch.object(client,'executables',return_value=(node,self.core)))
        self.enterContext(patch.object(client,'download_rules',side_effect=lambda:copy.deepcopy(self.data)))
        real=subprocess.run
        def run(args,*a,**kw):
            if args[0]==self.core:return subprocess.CompletedProcess(args,0,stdout=b'offline validation',stderr=b'')
            return real(args,*a,**kw)
        self.enterContext(patch.object(client.subprocess,'run',side_effect=run))

    def put(self,path,value):
        (self.root/path).write_text(client.yaml_dumps(value,allow_unicode=True,sort_keys=False),encoding='utf-8')

    def prepare(self):
        return client.prepare(self.root,'Exit','exit','SG',chain_spec=self.request)

    def apply(self):
        self.prepare()
        with patch.object(client,'api',return_value={}):client.apply_client()
        return client.load(self.local/'plan.json')

    def test_uses_the_same_rules_tun_and_target_dns_as_regular_apply(self):
        self.exit['rules'].insert(0,'IP-CIDR6,fe80::/10,DIRECT,no-resolve');self.put('profiles/exit.yaml',self.exit)
        _,_,normal,_=client.build(self.root,self.index['items'][0],'Exit',copy.deepcopy(self.data))
        self.prepare();chain=client.load(self.local/'candidate.yaml')
        self.assertEqual(chain['rules'],normal['rules'])
        self.assertEqual(chain['tun'],normal['tun'])
        self.assertEqual(chain['dns']['nameserver-policy'],normal['dns']['nameserver-policy'])
        self.assertEqual(chain['dns']['nameserver'],normal['dns']['nameserver'])
        self.assertEqual(chain['ipv6'],normal['ipv6'])
        self.assertEqual(chain['proxy-groups'][-1]['proxies'],['Entry-Exit'])
        self.assertEqual(chain['proxies'][0]['dialer-proxy'],managed_chain.FRONT)
        self.assertIn('entry.example.invalid',chain['dns']['proxy-server-nameserver-policy'])

    def test_apply_preserves_originals_and_reuses_first_baseline_and_source_identity(self):
        originals={p:p.read_bytes() for p in (self.root/'profiles/exit.yaml',self.root/'profiles/front.yaml')}
        plan=self.apply();baseline=(Path(plan['snapshot'])/'manifest.json').read_bytes()
        self.assertEqual(plan['region'],'SG');self.assertEqual(plan['node'],'Entry-Exit')
        self.assertEqual({p:p.read_bytes() for p in originals},originals)
        choices=client.discover(self.root)['subscriptions']
        self.assertEqual(next(r for r in choices if r.get('chain'))['nodes'],['Entry-Exit'])
        result=client.prepare(self.root,plan['node'],plan['subscription_uid'],'SG')
        self.assertEqual(result['snapshot'],plan['snapshot'])
        self.assertEqual((Path(plan['snapshot'])/'manifest.json').read_bytes(),baseline)
        self.assertEqual(client.load(self.local/'plan.json')['subscription_uid'],plan['subscription_uid'])

    def test_missing_node_and_same_hop_stop_without_new_profiles(self):
        self.request['front_node']='Missing'
        with self.assertRaises(AssertionError):self.prepare()
        self.assertEqual(client.load(self.root/'profiles.yaml'),self.index)
        self.request.update(front_subscription='exit',front_node='Exit')
        with self.assertRaises(AssertionError):self.prepare()

    def test_failed_publish_rolls_back_index_runtime_source_and_registration(self):
        self.prepare();before={p:p.read_bytes() for p in (self.root/'profiles.yaml',self.root/'clash-verge.yaml')}
        with patch.object(client,'api',side_effect=[RuntimeError('offline failure'),{}]):
            with self.assertRaises(RuntimeError):client.apply_client()
        self.assertEqual({p:p.read_bytes() for p in before},before)
        self.assertFalse((self.local/managed_chain.REGISTRY).exists())
        self.assertFalse(any(p.name.startswith('ManagedChain') for p in (self.root/'profiles').iterdir()))

    def test_restoration_detaches_wrapper_and_keeps_latest_local_and_remote_sources(self):
        plan=self.apply();profiles=client.load(self.root/'profiles.yaml')
        self.front['proxies'][0]['port']=5555;self.put('profiles/front.yaml',self.front)
        journal=client.load(Path(plan['snapshot'])/'client-changes.json')
        targets,remove,restored=client.client_restore_targets(self.root,Path(plan['snapshot'])/'client',plan,journal)
        entry=next(i for i in restored['items'] if i['uid']==plan['subscription_uid'])
        self.assertNotIn('script',entry.get('option',{}))
        self.assertNotIn(self.root/'profiles/front.yaml',targets)
        self.assertNotIn(self.root/'profiles'/plan['chain']['file'],remove)
        self.assertEqual(restored['current'],profiles['current'])

    def test_bypass_and_wrong_direction_are_rejected(self):
        self.prepare();original=client.load(self.local/'managed-chain-source.yaml');candidate=client.load(self.local/'candidate.yaml')
        for mutate in (lambda c:c['proxies'][0].pop('dialer-proxy'),
                       lambda c:c['proxies'][1].update({'dialer-proxy':'Entry-Exit'}),
                       lambda c:c['proxy-groups'][-1]['proxies'].append('DIRECT'),
                       lambda c:c['dns'].pop('proxy-server-nameserver-policy')):
            value=copy.deepcopy(candidate);mutate(value)
            with self.assertRaises(AssertionError):client.validate(value,original,'Entry-Exit')

    def test_native_reapply_wrapper_rejects_a_removed_front_hop(self):
        self.prepare();original=client.load(self.local/'managed-chain-source.yaml')
        original['proxies'][0].pop('dialer-proxy')
        runner="const fs=require('fs'),vm=require('vm');vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>main(JSON.parse(s)));"
        result=subprocess.run([str(client.PROJECT/'.runtime/node/node.exe'),'-e',runner,str(self.local/'candidate.js')],
                              input=json.dumps(original),text=True,encoding='utf-8',capture_output=True,timeout=15)
        self.assertNotEqual(result.returncode,0)

    def test_loaded_direction_is_checked_without_network_probes(self):
        plan={'node':'Entry-Exit','chain':{'file':'fixture'}}
        live={'proxies':{'Claude-Overseas-Pinned':{'all':['Entry-Exit'],'now':'Entry-Exit'},
                         'Entry-Exit':{'dialer-proxy':managed_chain.FRONT},managed_chain.FRONT:{'dialer-proxy':''}}}
        managed_chain.assert_live(plan,live)
        live['proxies']['Entry-Exit']['dialer-proxy']=''
        with self.assertRaises(AssertionError):managed_chain.assert_live(plan,live)

    def update_api(self,root,method,path,body=None):
        if method=='PUT':return {}
        if path=='/configs':return {'mode':'rule','find-process-mode':'strict'}
        if path=='/rules':return {'rules':[{'type':'GeoSite','payload':'geolocation-!cn','proxy':client.GROUP},
                                          {'type':'RuleSet','payload':'strict-cn-domain','proxy':'DIRECT'}]}
        if path=='/providers/rules':return {'providers':{k:{'ruleCount':len(v['payload'])} for k,v in client.load(self.root/'clash-verge.yaml')['rule-providers'].items()}}
        if path=='/proxies':
            node=client.load(self.root/'clash-verge.yaml')['x-portable-chain']['name']
            return {'proxies':{client.GROUP:{'all':[node],'now':node},
                    node:{'dialer-proxy':managed_chain.FRONT},managed_chain.FRONT:{'dialer-proxy':''}}}
        raise AssertionError('Unexpected offline controller request')

    def run_update(self):
        with patch.object(update,'LOCAL',self.local),patch.object(update,'download_rules',side_effect=lambda:copy.deepcopy(self.data)),\
             patch.object(update,'api',side_effect=self.update_api):
            return update.update()

    def test_rule_update_refreshes_both_hops_without_changing_original_nodes(self):
        plan=self.apply()
        self.front['proxies'][0]['port']=5555;self.put('profiles/front.yaml',self.front)
        self.exit['proxies'][0]['port']=6666;self.put('profiles/exit.yaml',self.exit)
        result=self.run_update()
        self.assertTrue(result['chain_profile_updated']);self.assertTrue(result['active_profile_reloaded'])
        raw=client.load(self.root/'profiles'/plan['chain']['file'])
        self.assertEqual([p['port'] for p in raw['proxies']],[6666,5555])
        self.assertEqual(client.load(self.root/'profiles/front.yaml'),self.front)
        self.assertEqual(client.load(self.root/'profiles/exit.yaml'),self.exit)
        self.assertTrue(self.run_update()['unchanged'])

    def test_rule_update_failure_restores_source_registration_script_and_plan(self):
        plan=self.apply();paths=[self.root/'profiles'/plan['chain']['file'],self.root/'profiles'/plan['script_file'],
                                self.local/managed_chain.REGISTRY,self.local/'plan.json',self.root/'clash-verge.yaml']
        before={p:p.read_bytes() for p in paths};self.front['proxies'][0]['port']=5555;self.put('profiles/front.yaml',self.front)
        original=self.update_api
        def mismatch(root,method,path,body=None):
            return {'providers':{}} if path=='/providers/rules' else original(root,method,path,body)
        with patch.object(self,'update_api',side_effect=mismatch),self.assertRaises(AssertionError):self.run_update()
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        self.assertFalse((self.local/'rule-update-pending.json').exists())

    def test_gui_passes_only_the_exact_chain_selection_under_local_job_folder(self):
        app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
        with patch.object(app,'configured',return_value=(False,None)),patch.object(app,'plan',return_value={}):
            action,params=app._validate_job({'action':'apply','confirm':'APPLY','node':'Exit','subscription':'exit','region':'SG','chain':self.request})
        folder=self.local/'job';folder.mkdir()
        command,_=app._job_command(action,params,folder)
        path=Path(command[command.index('-ChainSpecPath')+1])
        self.assertTrue(path.is_relative_to(self.local));self.assertEqual(client.load(path),self.request)
        with patch.object(app,'configured',return_value=(False,None)),patch.object(app,'plan',return_value={}):
            with self.assertRaises(gui_server.ApiError):
                app._validate_job({'action':'apply','confirm':'APPLY','node':'Wrong','region':'SG','chain':self.request})

    def test_other_chain_and_active_companion_receive_the_same_rule_update(self):
        first=self.apply();self.request['name']='Second chain';second=self.apply()
        index=client.load(self.root/'profiles.yaml');index['current']=first['subscription_uid'];self.put('profiles.yaml',index)
        self.front['proxies'][0]['port']=7777;self.put('profiles/front.yaml',self.front)
        self.data['domains'].append('+.example.cn')
        result=self.run_update();self.assertTrue(result['active_profile_reloaded'])
        for plan in (first,second):
            self.assertEqual(client.load(self.root/'profiles'/plan['chain']['file'])['proxies'][1]['port'],7777)
            wrapper=(self.root/'profiles'/plan['script_file']).read_text(encoding='utf-8')
            self.assertIn('+.example.cn',client.parse_project_wrapper(wrapper)[2]['domains'])
        self.assertEqual(client.load(self.root/'clash-verge.yaml')['x-portable-chain']['name'],'Entry-Exit')
        self.assertEqual(client.load(self.local/'plan.json')['node'],'Second chain')

    def test_chain_updates_continue_after_normal_profile_becomes_the_primary_plan(self):
        first=self.apply()
        client.prepare(self.root,'Exit','exit','SG')
        with patch.object(client,'api',return_value={}):client.apply_client()
        self.front['proxies'][0]['port']=8888;self.put('profiles/front.yaml',self.front)
        result=self.run_update();self.assertTrue(result['chain_profile_updated'])
        self.assertEqual(client.load(self.root/'profiles'/first['chain']['file'])['proxies'][1]['port'],8888)

    def test_single_then_chain_then_single_preserves_both_configurations(self):
        client.prepare(self.root,'Exit','exit','SG')
        with patch.object(client,'api',return_value={}):client.apply_client()
        normal_plan=client.load(self.local/'plan.json')
        normal_entry=copy.deepcopy(next(i for i in client.load(self.root/'profiles.yaml')['items'] if i['uid']=='exit'))
        normal_script=self.root/'profiles'/normal_plan['script_file'];normal_bytes=normal_script.read_bytes()
        baseline=(Path(normal_plan['snapshot'])/'manifest.json').read_bytes()
        chain_plan=self.apply()
        current=client.load(self.root/'profiles.yaml')
        self.assertEqual(next(i for i in current['items'] if i['uid']=='exit'),normal_entry)
        self.assertEqual(normal_script.read_bytes(),normal_bytes)
        chain_source=self.root/'profiles'/chain_plan['chain']['file'];chain_bytes=chain_source.read_bytes()
        client.prepare(self.root,'Exit','exit','SG')
        with patch.object(client,'api',return_value={}):client.apply_client()
        choices=client.discover(self.root)['subscriptions']
        self.assertTrue(any(row['uid']=='exit' and row['nodes']==['Exit'] for row in choices))
        self.assertTrue(any(row['uid']==chain_plan['subscription_uid'] and row.get('chain') for row in choices))
        self.assertEqual(chain_source.read_bytes(),chain_bytes)
        self.assertEqual(client.load(self.root/'profiles.yaml')['current'],'exit')
        self.assertEqual((Path(normal_plan['snapshot'])/'manifest.json').read_bytes(),baseline)


if __name__=='__main__':unittest.main()
