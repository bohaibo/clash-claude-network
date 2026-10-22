"""Restore only proxy fields; preserve later Git/npm/Docker account settings."""
import json,re,shutil,subprocess,uuid
from pathlib import Path
from baseline import FINAL,snapshot,verify

GIT_PROXY=r'^https?(\..*)?\.proxy$'
NPM_PROXY=re.compile(r"^\s*['\"]?(?:proxy|https-proxy)['\"]?\s*=",re.I)

def git_proxy_values(git,file):
    if not file.exists():return []
    result=subprocess.run([git,'config','--no-includes','--file',str(file),'--null','--get-regexp',GIT_PROXY],capture_output=True)
    if result.returncode not in (0,1):raise RuntimeError('Cannot inspect Git proxy fields')
    return [tuple(value.decode('utf-8').split('\n',1)) for value in result.stdout.split(b'\0') if value]

def restore_apps():
    verify();state=snapshot();saved=json.loads((state/'system.json').read_text(encoding='utf-8-sig'))
    work=FINAL/('app-proxy-restore-'+uuid.uuid4().hex);work.mkdir()
    failures=[];changed=[]
    for index,(name,entry) in enumerate(saved.get('appFiles',{}).items()):
        current=Path(entry['path']);original=state/entry['copy']
        try:
            before=current.read_bytes() if current.exists() else b''
            old=original.read_bytes() if entry['exists'] else b''
            if not before and not old:continue
            (work/(str(index)+'-current.original')).write_bytes(before)
            if name=='.gitconfig':
                git=shutil.which('git')
                if not git:raise RuntimeError('Git is required to restore Git proxy fields')
                candidate=work/(str(index)+'-candidate.gitconfig');candidate.write_bytes(before)
                for key in dict.fromkeys(key for key,value in git_proxy_values(git,candidate)):
                    result=subprocess.run([git,'config','--file',str(candidate),'--unset-all',key],capture_output=True)
                    if result.returncode!=0:raise RuntimeError('Cannot remove current Git proxy field')
                for key,value in git_proxy_values(git,original) if entry['exists'] else []:
                    result=subprocess.run([git,'config','--file',str(candidate),'--add',key,value],capture_output=True)
                    if result.returncode!=0:raise RuntimeError('Cannot restore original Git proxy field')
                after=candidate.read_bytes()
            elif name=='.npmrc':
                current_lines=before.decode('utf-8-sig').splitlines(keepends=True)
                old_lines=old.decode('utf-8-sig').splitlines(keepends=True)
                kept=''.join(line for line in current_lines if not NPM_PROXY.match(line))
                proxies=''.join(line for line in old_lines if NPM_PROXY.match(line))
                after=(kept+('\n' if kept and not kept.endswith(('\n','\r')) and proxies else '')+proxies).encode('utf-8')
            elif name.replace('\\','/')=='.docker/config.json':
                value=json.loads(before.decode('utf-8-sig')) if before else {}
                previous=json.loads(old.decode('utf-8-sig')) if old else {}
                if not isinstance(value,dict) or not isinstance(previous,dict):raise ValueError('Docker config must be an object')
                if 'proxies' in previous:value['proxies']=previous['proxies']
                else:value.pop('proxies',None)
                after=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
            else:continue
            if after!=before:
                current.parent.mkdir(parents=True,exist_ok=True);current.write_bytes(after);changed.append(name)
        except Exception as exc:failures.append({'file':name,'reason':type(exc).__name__})
    report={'success':not failures,'changed':changed,'failed_items':failures,'recovery_directory':str(work)}
    (FINAL/'app-proxy-restore.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    result=restore_apps();print(json.dumps(result,ensure_ascii=True))
    raise SystemExit(0 if result['success'] else 1)
