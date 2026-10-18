"""Edit only language preferences while browsers are closed; retain original fields."""
from pathlib import Path
from baseline import FINAL, snapshot, verify
import argparse,datetime,hashlib,json,os,subprocess

ROOTS={'Chrome':Path(os.environ['LOCALAPPDATA'])/'Google/Chrome/User Data',
       'Edge':Path(os.environ['LOCALAPPDATA'])/'Microsoft/Edge/User Data'}
FIELDS=('accept_languages','selected_languages')
TARGET='zh-SG,en-SG,zh-CN'
def backup_path():return snapshot()/'browser-languages-before.json'

def profiles():
    return [(browser,p) for browser,root in ROOTS.items() for p in root.glob('*/Preferences')
            if p.parent.name=='Default' or p.parent.name.startswith('Profile ')]

def atomic(path,value):
    temp=path.with_name(path.name+'.language-new')
    temp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    temp.replace(path)

def capture():
    manifest=verify();store=backup_path()
    saved=json.loads(store.read_text(encoding='utf-8')) if store.exists() else {
        'captured_at':datetime.datetime.now().astimezone().isoformat(),'profiles':{}}
    changed=False
    for browser,path in profiles():
        key=browser+'/'+path.parent.name
        if key in saved['profiles']:continue
        prefs=json.loads(path.read_text(encoding='utf-8'));intl=prefs.get('intl',{})
        saved['profiles'][key]={'browser':browser,'profile':path.parent.name,
            'fields':{name:{'exists':name in intl,'value':intl.get(name)} for name in FIELDS}}
        changed=True
    if changed:
        atomic(store,saved)
        manifest['sha256']['browser-languages-before.json']=hashlib.sha256(store.read_bytes()).hexdigest()
        manifest['coverage']['browserLanguages']='Only original per-profile language fields, captured before first preference edit; unrelated browser data is never restored from this file.'
        atomic(snapshot()/'manifest.json',manifest)
    verify();return saved

def running():
    command='@(Get-Process chrome,msedge -ErrorAction SilentlyContinue).Count'
    return int(subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True).strip())>0

def update(restore=False):
    assert not running(),'Close Chrome and Edge first. No live Preferences files were edited.'
    saved=capture();results=[]
    for browser,path in profiles():
        key=browser+'/'+path.parent.name
        if restore and key not in saved['profiles']:continue
        original=json.loads(path.read_text(encoding='utf-8'));prefs=json.loads(json.dumps(original))
        intl=prefs.setdefault('intl',{})
        for field in FIELDS:
            if restore:
                old=saved['profiles'][key]['fields'][field]
                if old['exists']:intl[field]=old['value']
                else:intl.pop(field,None)
            else:intl[field]=TARGET
        # Verify that no keys outside the two targeted language fields changed.
        before=json.loads(json.dumps(original));after=json.loads(json.dumps(prefs))
        for value in (before,after):
            for field in FIELDS:value.setdefault('intl',{}).pop(field,None)
        assert before==after,'Unexpected unrelated preference change'
        atomic(path,prefs)
        results.append({'browser':browser,'profile':path.parent.name,'intl':intl})
    report={'success':True,'restored':restore,'profiles':results,'browser_data_preserved':True}
    atomic(FINAL/('browser-languages-restore.json' if restore else 'browser-languages-result.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('capture','apply','restore'));args=p.parse_args()
    print(json.dumps(capture() if args.action=='capture' else update(args.action=='restore'),ensure_ascii=False))
