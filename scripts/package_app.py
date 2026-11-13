"""Build a clean portable ZIP from an explicit source allowlist."""
import argparse,hashlib,json,re,uuid,zipfile
from pathlib import Path
from app_release import metadata
from client import PROJECT,LOCAL,load,write
from checks import require

ROOT_FILES={'一键网络.cmd','README.md','INSTALL.md','LICENSE','AGENTS.md','SKILL.md','requirements.txt'}
BLOCKED={'.local','.runtime','.git','__pycache__','node_modules','.venv'}

def package_files(project):
 project=Path(project).resolve();names=load(project/'assets/package-files.json')
 require(isinstance(names,list) and all(isinstance(name,str) for name in names) and len(set(names))==len(names),'Invalid package allowlist')
 require(ROOT_FILES<=set(names) and {'scripts/gui_server.py','scripts/bootstrap.ps1','assets/app-release.json','assets/package-files.json','gui/index.html'}<=set(names),'Incomplete package allowlist')
 result=[]
 for name in sorted(names):
  relative=Path(name)
  require('\\' not in name and not relative.is_absolute() and '..' not in relative.parts and not BLOCKED.intersection(relative.parts),'Private or unsafe package path')
  allowed=name in ROOT_FILES or (len(relative.parts)>1 and relative.parts[0] in ('scripts','gui','assets','references','agents') and relative.suffix in ('.py','.ps1','.js','.css','.html','.svg','.json','.md','.yaml'))
  require(allowed,'File type not approved for packaging')
  path=project/relative
  require(path.is_file() and path.resolve().is_relative_to(project),'Missing or external package file')
  require(not any(part.is_symlink() or part.is_junction() for part in (path,*path.parents) if part.is_relative_to(project)),'Linked package file rejected')
  result.append((name,path))
 return result

def build_package(project=PROJECT,local=LOCAL):
 project=Path(project);local=Path(local);info=metadata(project)
 if (project/'assets/webrtc-addon/bundle.json').exists():
  from webrtc_bundle import payload as addon_payload
  addon_payload(project)
 forbidden=[]
 try:forbidden=load(local/'publish-forbidden-values.json')
 except FileNotFoundError:pass
 payload={}
 for name,path in package_files(project):
  raw=path.read_bytes();require(len(raw)<=4_000_000,'Oversized package source')
  text=raw.decode('utf-8-sig')
  require(not re.search(r'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----|S-1-5-21-\d+-\d+-\d+-\d+)',text),'Private data in package source')
  require(not any(value and value in text for value in forbidden),'Machine-local data in package source')
  payload[name]=raw
 manifest={'version':info['version'],'repository':info['repository'],'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in payload.items()}}
 token=uuid.uuid4().hex;folder=local/'packages'/token;folder.mkdir(parents=True)
 filename='clash-claude-network-'+info['version']+'-windows.zip';target=folder/filename
 try:
  with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
   prefix='clash-claude-network/'
   for name,raw in payload.items():archive.writestr(prefix+name,raw)
   archive.writestr(prefix+'package-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
  with zipfile.ZipFile(target) as archive:require(archive.testzip() is None,'Package verification failed')
  digest=hashlib.sha256(target.read_bytes()).hexdigest()
  (folder/(filename+'.sha256')).write_text(digest+'  '+filename+'\n',encoding='ascii')
  result={'id':token,'filename':filename,'version':info['version'],'size':target.stat().st_size,'sha256':digest,'file_count':len(payload)}
  write(folder/'result.json',result);return result
 except Exception:target.unlink(missing_ok=True);raise

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.parse_args()
 from operation_lock import operation_lock
 with operation_lock(LOCAL):result=build_package()
 print(json.dumps(result,ensure_ascii=False))
