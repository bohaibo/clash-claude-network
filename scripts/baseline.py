from pathlib import Path
import json,hashlib,os,socket
FINAL=Path(__file__).resolve().parent.parent/'.local'
def snapshot():
 value=os.environ.get('CCN_SNAPSHOT')
 if value:return Path(value)
 roots=sorted([p for p in (FINAL/'snapshots').glob('*') if (p/'manifest.json').is_file()],key=lambda p:p.name,reverse=True)
 assert roots,'No snapshot captured yet; run apply once to create the first snapshot'
 return roots[0]
def verify():
 path=snapshot();m=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
 assert m['computer']==socket.gethostname(),'Backup belongs to another computer'
 import subprocess,re
 sid=re.search(r'S-1-\d+(?:-\d+)+',subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True)).group(0)
 assert m['user_sid']==sid,'Backup belongs to another Windows user'
 for name,digest in m['sha256'].items():
  p=path/'client'/name
  if not p.exists():p=path/name
  assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,'Backup integrity failure'
 for name,digest in m.get('extra_sha256',{}).items():
  assert hashlib.sha256((path/name).read_bytes()).hexdigest()==digest,'System backup integrity failure'
 return m
