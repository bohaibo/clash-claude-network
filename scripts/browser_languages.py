"""Edit only language preferences while browsers are closed; retain original fields."""
from checks import require
from pathlib import Path
from baseline import FINAL, snapshot, verify
import argparse,datetime,hashlib,json,os,subprocess,uuid

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

def read_saved(manifest):
    store=backup_path()
    if store.exists() and store.name not in manifest['sha256'] and store.name not in manifest.get('extra_sha256',{}):
        raise ValueError('Browser language backup is not committed in the manifest')
    if not store.exists() and manifest.get('incremental_sha256'):
        raise ValueError('Original browser language backup is missing')
    saved=json.loads(store.read_text(encoding='utf-8')) if store.exists() else {
        'captured_at':datetime.datetime.now().astimezone().isoformat(),'profiles':{}}
    for name in manifest.get('incremental_sha256',{}):
        increment=json.loads((snapshot()/name).read_text(encoding='utf-8'))
        for key,record in increment['profiles'].items():
            if key in saved['profiles']:raise ValueError('Duplicate browser profile backup')
            saved['profiles'][key]=record
    return saved

def capture():
    manifest=verify();store=backup_path();saved=read_saved(manifest)
    additions={}
    for browser,path in profiles():
        key=browser+'/'+path.parent.name
        if key in saved['profiles']:continue
        prefs=json.loads(path.read_text(encoding='utf-8'));intl=prefs.get('intl',{})
        additions[key]={'browser':browser,'profile':path.parent.name,
            'fields':{name:{'exists':name in intl,'value':intl.get(name)} for name in FIELDS}}
    if not store.exists():
        # The first backup is immutable, including after it has been sealed.
        saved['profiles'].update(additions)
        atomic(store,saved)
        manifest['sha256']['browser-languages-before.json']=hashlib.sha256(store.read_bytes()).hexdigest()
        manifest['coverage']['browserLanguages']='Only original per-profile language fields, captured before first preference edit; unrelated browser data is never restored from this file.'
        atomic(snapshot()/'manifest.json',manifest)
    elif additions:
        folder=snapshot()/'browser-language-increments';folder.mkdir(exist_ok=True)
        target=folder/(uuid.uuid4().hex+'.json')
        atomic(target,{'captured_at':datetime.datetime.now().astimezone().isoformat(),'profiles':additions})
        # Commit the new file's hash without changing any existing backup/hash.
        name=target.relative_to(snapshot()).as_posix()
        manifest.setdefault('incremental_sha256',{})[name]=hashlib.sha256(target.read_bytes()).hexdigest()
        atomic(snapshot()/'manifest.json',manifest)
    return read_saved(verify())

def running():
    command='@(Get-Process chrome,msedge -ErrorAction SilentlyContinue).Count'
    return int(subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True).strip())>0

def update(restore=False):
    require(not running(),'请先关闭 Chrome 和 Edge 再重试。没有改动任何正在使用的 Preferences 文件。')
    # Restoration only reads committed backups; it never captures today's state.
    saved=read_saved(verify()) if restore else capture();results=[]
    for browser,path in profiles():
        key=browser+'/'+path.parent.name
        if key not in saved['profiles']:
            if restore:continue
            raise RuntimeError('New browser profile appeared after capture; retry before editing it')
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
        require(before==after,'Unexpected unrelated preference change')
        atomic(path,prefs)
        results.append({'browser':browser,'profile':path.parent.name,'intl':intl})
    report={'success':True,'restored':restore,'profiles':results,'browser_data_preserved':True}
    atomic(FINAL/('browser-languages-restore.json' if restore else 'browser-languages-result.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('capture','apply','restore'));args=p.parse_args()
    print(json.dumps(capture() if args.action=='capture' else update(args.action=='restore'),ensure_ascii=False))
