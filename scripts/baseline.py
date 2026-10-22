from checks import require
from pathlib import Path
import json,hashlib,os,socket
FINAL=Path(__file__).resolve().parent.parent/'.local'
# Apply and supplement accumulate these write-ahead recovery journals after sealing.
# They describe changes to undo, rather than immutable baseline contents.
RUNTIME_SNAPSHOT_FILES={'disabled-by-apply.json','firewall-disabled-by-apply.json','client-changes.json','winhttp.exec.txt'}

def is_runtime_snapshot_file(name):
 return name in RUNTIME_SNAPSHOT_FILES or name.endswith(('.tmp','.new','.language-new'))

def verify_incremental_files(path,manifest):
 # Only committed increments are consumed. A crash before manifest publication
 # leaves an unused file, never a partially committed browser backup.
 for name,digest in manifest.get('incremental_sha256',{}).items():
  relative=Path(name)
  if (len(relative.parts)!=2 or relative.parts[0]!='browser-language-increments'
      or relative.suffix!='.json' or not (path/relative).resolve().is_relative_to(path.resolve())):
   raise ValueError('Invalid incremental backup path')
  if hashlib.sha256((path/relative).read_bytes()).hexdigest()!=digest:
   raise ValueError('Incremental browser backup integrity failure')
def default_snapshot(local):
 roots=sorted([p for p in (local/'snapshots').glob('*') if (p/'manifest.json').is_file()],key=lambda p:p.name,reverse=True)
 if not roots:return None
 automatic=[p for p in roots if json.loads((p/'manifest.json').read_text(encoding='utf-8')).get('kind','auto')=='auto']
 return (automatic or roots)[0]

def snapshot():
 value=os.environ.get('CCN_SNAPSHOT')
 if value:return Path(value)
 selected=default_snapshot(FINAL)
 require(selected,'No snapshot captured yet; run apply once to create the first snapshot')
 return selected

def preflight_restore():
 m=verify();path=snapshot()
 # All read-only checks precede stopping browsers or changing system state.
 if not Path(m['client_root']).is_dir():raise ValueError('Original client directory is missing')
 for name in ('browser-policies-before.json','browser-languages-before.json','system.json'):
  file=path/name
  if file.exists():json.loads(file.read_text(encoding='utf-8-sig'))
  if name=='system.json' and file.exists() and name not in m.get('extra_sha256',{}) and name not in m.get('sha256',{}):
   raise ValueError('System snapshot is not sealed; restoration stopped')
 plan=FINAL/'plan.json'
 if plan.exists():
  current=json.loads(plan.read_text(encoding='utf-8'))
  if Path(current['root']).resolve()!=Path(m['client_root']).resolve():
   raise ValueError('Plan belongs to a different client directory')
 # Validate the subscription merge before changing system state or closing apps.
 from client import client_restore_targets
 journal=path/'client-changes.json'
 changes=json.loads(journal.read_text(encoding='utf-8-sig')) if journal.exists() else {}
 client_restore_targets(Path(m['client_root']),path/'client',current if plan.exists() else {},changes)
 return m
def verify():
 path=snapshot();m=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
 require(m['computer']==socket.gethostname(),'Backup belongs to another computer')
 import subprocess,re
 sid=re.search(r'S-1-\d+(?:-\d+)+',subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True)).group(0)
 require(m['user_sid']==sid,'Backup belongs to another Windows user')
 for name,digest in m['sha256'].items():
  p=path/'client'/name
  if not p.exists():p=path/name
  require(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,'Backup integrity failure')
 for name,digest in m.get('extra_sha256',{}).items():
  if is_runtime_snapshot_file(name):continue
  require(hashlib.sha256((path/name).read_bytes()).hexdigest()==digest,'System backup integrity failure')
 verify_incremental_files(path,m)
 return m
