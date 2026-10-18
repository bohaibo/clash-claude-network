"""Snapshot regressions; fixtures and logs stay under .local/, never touch live settings."""
import ctypes,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import baseline,client

class Snapshots(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='snapshot-test-')
  self.addCleanup(self.temp.cleanup)
  self.local=Path(self.temp.name);self.root=self.local/'fake-client';self.root.mkdir()
  for name in ('profiles.yaml','clash-verge.yaml'):(self.root/name).write_text('{}',encoding='utf-8')
  self.enterContext(patch.object(client,'LOCAL',self.local))
  self.enterContext(patch.object(client,'SNAPSHOTS',self.local/'snapshots'))
  self.enterContext(patch.object(baseline,'FINAL',self.local))
  self.enterContext(patch.dict(os.environ))
  os.environ.pop('CCN_SNAPSHOT',None)
 def exclusive_lock(self,path):
  path.write_text('runtime',encoding='utf-8')
  create=ctypes.windll.kernel32.CreateFileW
  create.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
  create.restype=ctypes.c_void_p
  handle=create(str(path),0x80000000,0,None,3,0,None)
  self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
  close=ctypes.windll.kernel32.CloseHandle;close.argtypes=[ctypes.c_void_p]
  self.addCleanup(close,handle)
 def test_running_client_lock_and_restore_without_plan(self):
  self.exclusive_lock(self.root/'singleton-instance.lock')
  (self.root/'logs').mkdir();(self.root/'logs'/'log.txt').write_text('volatile')
  snapshot=Path(client.create_snapshot(self.root)['snapshot'])
  self.assertFalse((snapshot/'client'/'singleton-instance.lock').exists())
  self.assertFalse((snapshot/'client'/'logs').exists())
  os.environ['CCN_SNAPSHOT']=str(snapshot)
  (snapshot/'system.json').write_text('{}')
  client.seal();baseline.verify()
  (self.root/'profiles.yaml').write_text('changed')
  with patch.object(client,'api',return_value={}) as api:
   self.assertTrue(client.restore_client()['client_restored'])
   api.assert_called_once()
  self.assertEqual((self.root/'profiles.yaml').read_text(),'{}')
  (snapshot/'system.json').write_text('tampered')
  with self.assertRaises(AssertionError):baseline.verify()
 def test_locked_configuration_is_not_silently_skipped(self):
  self.exclusive_lock(self.root/'profiles.yaml')
  with self.assertRaises(OSError):client.create_snapshot(self.root)
  self.assertEqual(client.snapshot_roots(),[])
 def test_manual_snapshot_preserves_first_automatic_backup(self):
  original=client.snapshot_dir(create=True);client.backup(self.root,original)
  manifest=(original/'manifest.json').read_bytes()
  (self.root/'profiles.yaml').write_text('changed')
  manual=Path(client.create_snapshot(self.root)['snapshot'])
  self.assertNotEqual(manual,original)
  self.assertEqual(client.snapshot_dir(),original)
  self.assertEqual((original/'manifest.json').read_bytes(),manifest)
  self.assertEqual((original/'client'/'profiles.yaml').read_text(),'{}')

if __name__=='__main__':unittest.main()
