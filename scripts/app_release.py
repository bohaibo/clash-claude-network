"""Stable software update notices. No installation or account credentials."""
import datetime,json,re,threading,urllib.error,urllib.request
from pathlib import Path
from client import PROJECT,LOCAL,load,write,root_path

def version_tuple(value):
 if not isinstance(value,str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',value):
  raise ValueError('Unsupported stable version')
 return tuple(int(part) for part in value.removeprefix('v').split('.'))

def metadata(project=PROJECT):
 value=load(Path(project)/'assets/app-release.json')
 version_tuple(value['version'])
 if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',value['repository']):raise ValueError('Invalid repository')
 if value.get('channel')!='stable':raise ValueError('Unsupported release channel')
 return value

VERSION=metadata()['version']

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise urllib.error.URLError('Release redirect refused')

class NoPublishedRelease(ValueError):
 pass

def github_bytes(repository,suffix):
 url='https://api.github.com/repos/'+repository+suffix
 request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'ClashNetwork/'+VERSION})
 routes=[{}]
 try:
  config=load(root_path()/'clash-verge.yaml') or {}
  port=config.get('mixed-port') or config.get('port')
  if type(port) is int and 0<port<65536 and not config.get('authentication'):
   routes.append({'https':'http://127.0.0.1:'+str(port)})
 except (OSError,ValueError,KeyError):pass
 for route in routes:
  try:
   opener=urllib.request.build_opener(urllib.request.ProxyHandler(route),NoRedirect())
   with opener.open(request,timeout=8) as response:raw=response.read(262145)
   if len(raw)>262144:raise ValueError('Release metadata too large')
   return raw
  except urllib.error.HTTPError:raise
  except OSError:continue
 raise OSError('Release source unreachable')

def release_bytes(repository):
 try:return github_bytes(repository,'/releases/latest')
 except urllib.error.HTTPError as exc:
  if exc.code!=404:raise
  # A visible repository with no latest stable release is not a network error.
  info=json.loads(github_bytes(repository,''))
  if isinstance(info,dict) and info.get('full_name')==repository and info.get('private') is False:
   raise NoPublishedRelease() from None
  raise

def parse_release(raw,info):
 release=json.loads(raw)
 if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
  raise ValueError('Not a published stable release')
 tag=release.get('tag_name');latest=version_tuple(tag)
 expected='https://github.com/'+info['repository']+'/releases/tag/'+tag
 if release.get('html_url')!=expected:raise ValueError('Untrusted release URL')
 return {'status':'available' if latest>version_tuple(info['version']) else 'current',
         'latest_version':tag.removeprefix('v'),'release_url':expected,
         'notes':str(release.get('body') or '')[:4000],'published_at':str(release.get('published_at') or '')[:40]}

class ReleaseChecker:
 def __init__(self,project=PROJECT,local=LOCAL):
  self.info=metadata(project);self.local=Path(local);self.lock=threading.Lock();self.checking=False
 def state(self):
  value={}
  try:
   cached=json.loads((self.local/'software-update.json').read_text(encoding='utf-8-sig'))
   if isinstance(cached,dict) and cached.get('repository')==self.info['repository'] and cached.get('current_version')==self.info['version'] and cached.get('status') in ('available','current','no_release','error'):
    value={key:cached[key] for key in ('status','reason','checked_at','latest_version','release_url','notes','published_at') if key in cached}
    if value['status'] in ('available','current'):
     version_tuple(value['latest_version'])
     prefix='https://github.com/'+self.info['repository']+'/releases/tag/'
     if value.get('release_url') not in (prefix+value['latest_version'],prefix+'v'+value['latest_version']):value={}
  except (OSError,ValueError,KeyError,TypeError):value={}
  return dict(value,current_version=self.info['version'],repository=self.info['repository'],checking=self.checking,
              status=value.get('status','unchecked'))
 def check(self,force=False):
  if not self.lock.acquire(blocking=False):return self.state()
  try:
   previous=self.state()
   try:
    elapsed=(datetime.datetime.now().astimezone()-datetime.datetime.fromisoformat(previous['checked_at'])).total_seconds()
    if not force and 0<=elapsed<86400:return previous
   except (KeyError,ValueError,TypeError):pass
   self.checking=True
   result={'status':'error','reason':'无法检查软件更新，请检查网络后重试。'}
   try:result=parse_release(release_bytes(self.info['repository']),self.info)
   except NoPublishedRelease:
    result={'status':'no_release','reason':'仓库尚未发布正式版本。'}
   except urllib.error.HTTPError as exc:
    result['reason']='发布源不可访问：可能尚未发布正式版本、仓库为私有，或访问受限。' if exc.code==404 else '更新服务暂不可用或访问受限，请稍后重试。'
   except Exception:pass
   result.update(current_version=self.info['version'],repository=self.info['repository'],checked_at=datetime.datetime.now().astimezone().isoformat())
   write(self.local/'software-update.json',result)
   return result
  finally:self.checking=False;self.lock.release()
