import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import update

class UpdateFailure(unittest.TestCase):
 def test_known_validation_has_chinese_reason_and_local_trace(self):
  with tempfile.TemporaryDirectory(dir=update.LOCAL) as folder,patch.object(update,'LOCAL',Path(folder)):
   try:raise AssertionError('Private/LAN source changed; review before publishing')
   except Exception as exc:result=update.failure_result(exc)
   self.assertFalse(result['success'])
   self.assertIn('局域网',result['reason'])
   self.assertIn('AssertionError',(Path(folder)/'update-error.log').read_text())
 def test_unknown_error_details_stay_local(self):
  with tempfile.TemporaryDirectory(dir=update.LOCAL) as folder,patch.object(update,'LOCAL',Path(folder)):
   try:raise ValueError('fixture-subscription-secret')
   except Exception as exc:result=update.failure_result(exc)
   self.assertNotIn('fixture-subscription-secret',str(result))
   self.assertIn('fixture-subscription-secret',(Path(folder)/'update-error.log').read_text())
