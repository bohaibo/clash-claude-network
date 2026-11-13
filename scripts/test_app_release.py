"""Offline software update and packaging regressions; no real requests."""
import copy,datetime,hashlib,json,tempfile,unittest,urllib.error,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
import app_release as release,package_app,client,gui_server

class SoftwareRelease(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='software-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.checker=release.ReleaseChecker(local=self.local)
  self.raw={'tag_name':'v2.4.0','html_url':'https://github.com/bohaibo/clash-claude-network/releases/tag/v2.4.0',
   'draft':False,'prerelease':False,'body':'Changes','published_at':'2026-11-14T00:00:00Z'}
 def test_numeric_versions_do_not_use_lexical_order(self):
  self.assertGreater(release.version_tuple('2.10.0'),release.version_tuple('2.9.0'))
  for value in ['v2.3.0-beta','2.3','2.03.0','2.3.0/path',True,None]:
   with self.subTest(value=value),self.assertRaises(ValueError):release.version_tuple(value)
 def test_new_stable_version_has_validated_release_link(self):
  result=release.parse_release(json.dumps(self.raw),self.checker.info)
  self.assertEqual(result['status'],'available');self.assertEqual(result['latest_version'],'2.4.0')
 def test_old_or_equal_version_never_prompts_update(self):
  for version in ['2.1.0','2.2.0','2.3.0']:
   raw=dict(self.raw,tag_name=version,html_url='https://github.com/bohaibo/clash-claude-network/releases/tag/'+version)
   self.assertEqual(release.parse_release(json.dumps(raw),self.checker.info)['status'],'current')
 def test_draft_prerelease_and_untrusted_links_are_rejected(self):
  for changes in [{'draft':True},{'prerelease':True},{'html_url':'https://example.invalid/release'}, {'html_url':'https://github.com/other/repo/releases/tag/v2.3.0'}]:
   with self.subTest(changes=changes),self.assertRaises(ValueError):release.parse_release(json.dumps(dict(self.raw,**changes)),self.checker.info)
 def test_startup_cache_limits_checks_but_manual_check_refreshes(self):
  with patch.object(release,'release_bytes',return_value=json.dumps(self.raw).encode()) as get:
   self.checker.check();self.checker.check();self.assertEqual(get.call_count,1)
   self.checker.check(force=True);self.assertEqual(get.call_count,2)
 def test_missing_private_or_unreachable_release_never_claims_latest(self):
  for error in [urllib.error.HTTPError('withheld',404,'not found',{},None),OSError('withheld'),ValueError('invalid payload')]:
   with self.subTest(error=type(error).__name__),patch.object(release,'release_bytes',side_effect=error):
    result=self.checker.check(force=True)
   self.assertEqual(result['status'],'error');self.assertNotIn('withheld',result['reason'])
 def test_visible_repository_without_stable_release_has_specific_status(self):
  missing=urllib.error.HTTPError('withheld',404,'not found',{},None)
  repo=json.dumps({'full_name':self.checker.info['repository'],'private':False}).encode()
  with patch.object(release,'github_bytes',side_effect=[missing,repo]) as get:
   result=self.checker.check(force=True)
  self.assertEqual(result['status'],'no_release')
  self.assertEqual(self.checker.state()['status'],'no_release')
  self.assertIn('尚未发布正式版本',result['reason'])
  self.assertEqual([call.args[1] for call in get.call_args_list],['/releases/latest',''])
 def test_inaccessible_repository_remains_an_error(self):
  missing=urllib.error.HTTPError('withheld',404,'not found',{},None)
  with patch.object(release,'github_bytes',side_effect=missing):
   self.assertEqual(self.checker.check(force=True)['status'],'error')
 def test_startup_force_retries_recent_failure(self):
  with patch.object(release,'release_bytes',side_effect=OSError('withheld')):
   self.checker.check(force=True)
  with patch.object(release,'release_bytes',return_value=json.dumps(self.raw).encode()) as get:
   self.assertEqual(self.checker.check(force=True)['status'],'available')
  get.assert_called_once()
 def test_invalid_cached_json_does_not_break_app_state(self):
  (self.local/'software-update.json').write_text('{bad')
  self.assertEqual(self.checker.state()['status'],'unchecked')
 def test_concurrent_checks_do_not_duplicate_requests(self):
  self.checker.lock.acquire()
  try:
   with patch.object(release,'release_bytes') as get:self.checker.check(force=True)
   get.assert_not_called()
  finally:self.checker.lock.release()
 def test_proxy_fallback_never_inherits_environment_or_credentials(self):
  root=self.local/'client';root.mkdir();client.write(root/'clash-verge.yaml',{'mixed-port':7890})
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{}'
  opener=Mock();opener.open.side_effect=[OSError('direct failed'),response]
  with patch.object(release,'root_path',return_value=root),patch.object(release.urllib.request,'build_opener',return_value=opener),patch.object(release.urllib.request,'ProxyHandler',side_effect=lambda routes:routes) as handler:
   self.assertEqual(release.release_bytes('bohaibo/clash-claude-network'),b'{}')
  self.assertEqual([call.args[0] for call in handler.call_args_list],[{}, {'https':'http://127.0.0.1:7890'}])
  request=opener.open.call_args.args[0];self.assertNotIn('Authorization',request.headers)

class PortablePackage(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='package-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.project=self.local/'source';self.project.mkdir()
  self.names=sorted(package_app.ROOT_FILES|{'scripts/gui_server.py','scripts/bootstrap.ps1','assets/app-release.json','assets/package-files.json','gui/index.html'})
  for name in self.names:
   path=self.project/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture',encoding='utf-8')
  client.write(self.project/'assets/app-release.json',release.metadata())
  self.manifest=self.project/'assets/package-files.json';client.write(self.manifest,self.names)
 def test_package_excludes_local_runtime_and_unapproved_source_files(self):
  for name in ['.local/secret.json','.runtime/cache.txt','scripts/not-approved.py']:
   p=self.project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('LOCAL-SECRET-FIXTURE')
  result=package_app.build_package(self.project,self.local)
  target=self.local/'packages'/result['id']/result['filename']
  self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),result['sha256'])
  with zipfile.ZipFile(target) as archive:
   names=archive.namelist();self.assertEqual(archive.testzip(),None)
   self.assertFalse(any('.local' in name or '.runtime' in name or 'not-approved' in name for name in names))
   manifest=json.loads(archive.read('clash-claude-network/package-manifest.json'))
   for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256(archive.read('clash-claude-network/'+name)).hexdigest(),digest)
 def test_allowlist_cannot_include_private_paths_or_traversal(self):
  for value in ['.local/secret.json','../secret.py','.runtime/python313/python.exe','scripts/../../secret.py',r'scripts\secret.py']:
   client.write(self.manifest,self.names+[value])
   with self.subTest(value=value),self.assertRaises(AssertionError):package_app.build_package(self.project,self.local)
 def test_forbidden_machine_values_abort_before_creating_zip(self):
  client.write(self.local/'publish-forbidden-values.json',['MACHINE-SECRET-FIXTURE'])
  (self.project/'README.md').write_text('MACHINE-SECRET-FIXTURE')
  with self.assertRaises(AssertionError):package_app.build_package(self.project,self.local)
  self.assertFalse((self.local/'packages').exists())
 def test_missing_required_file_never_produces_incomplete_package(self):
  (self.project/'scripts/gui_server.py').unlink()
  with self.assertRaises(AssertionError):package_app.build_package(self.project,self.local)
 def test_gui_export_respects_active_operation(self):
  app=gui_server.App(local=self.local,open_browser=False,idle_exit=False);app.active_job={'id':'fixture'}
  with patch.object(gui_server,'build_package') as build:
   with self.assertRaises(gui_server.ApiError):app.export_package()
  build.assert_not_called()
 def test_gui_download_rejects_unknown_ids(self):
  app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
  for identifier in ['../secret','a'*32]:
   with self.assertRaises(gui_server.ApiError):app.package_download(identifier)

if __name__=='__main__':unittest.main()
