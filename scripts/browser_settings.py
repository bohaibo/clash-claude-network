"""Current-user browser policies, with a first-change backup in the original baseline."""
import argparse, datetime, hashlib, json, winreg
from pathlib import Path
from baseline import FINAL, verify

ROOTS={'Chrome':r'Software\Policies\Google\Chrome','Edge':r'Software\Policies\Microsoft\Edge'}
VALUES={'Chrome':{'WebRtcIPHandling':'disable_non_proxied_udp','DnsOverHttpsMode':'off','ProxyMode':'direct'},
        'Edge':{'WebRtcLocalhostIpHandling':'disable_non_proxied_udp','DnsOverHttpsMode':'off',
                'ProxyMode':'direct','DefinePreferredLanguages':'zh-SG,en-SG,zh-CN'}}
BACKUP=FINAL/'baseline/browser-policies-before.json'

def read(path,name,hive=winreg.HKEY_CURRENT_USER):
    try:
        with winreg.OpenKey(hive,path) as key:value,kind=winreg.QueryValueEx(key,name)
        return {'exists':True,'value':value,'kind':kind}
    except FileNotFoundError:return {'exists':False}

def list_values(path):
    result={}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path) as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                name,value,kind=winreg.EnumValue(key,index);result[name]={'exists':True,'value':value,'kind':kind}
    except FileNotFoundError:pass
    return result

def capture():
    manifest=verify()
    if BACKUP.exists():return json.loads(BACKUP.read_text(encoding='utf-8'))
    state={'captured_at':datetime.datetime.now().astimezone().isoformat(),'scope':'Current Windows user; first browser change in this task series',
           'values':{label:{name:read(path,name) for name in VALUES[label]} for label,path in ROOTS.items()},
           'chrome_languages':list_values(ROOTS['Chrome']+r'\ForcedLanguages')}
    BACKUP.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest['sha256']['browser-policies-before.json']=hashlib.sha256(BACKUP.read_bytes()).hexdigest()
    manifest['coverage']['browserPolicies']='Values captured before the first browser policy changes on this machine.'
    target=FINAL/'baseline/manifest.json';temp=target.with_suffix('.new')
    temp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(target)
    verify();return state

def put(path,name,value,kind=winreg.REG_SZ):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key,name,0,kind,value)

def remove(path,name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_SET_VALUE) as key:winreg.DeleteValue(key,name)
    except FileNotFoundError:pass

def apply():
    capture()
    for label,path in ROOTS.items():
        for name,value in VALUES[label].items():
            machine=read(path,name,winreg.HKEY_LOCAL_MACHINE)
            assert not machine['exists'] or machine['value']==value,'Conflicting machine browser policy; manual review required'
    for label,path in ROOTS.items():
        for name,value in VALUES[label].items():put(path,name,value)
    path=ROOTS['Chrome']+r'\ForcedLanguages'
    for name in list_values(path):remove(path,name)
    for index,value in enumerate(('zh-SG','en-SG','zh-CN'),1):put(path,str(index),value)
    return status()

def restore():
    if not BACKUP.exists():return {'restored':False,'reason':'No browser changes captured'}
    verify();state=json.loads(BACKUP.read_text(encoding='utf-8'))
    for label,path in ROOTS.items():
        for name,value in state['values'][label].items():
            if value['exists']:put(path,name,value['value'],value['kind'])
            else:remove(path,name)
    path=ROOTS['Chrome']+r'\ForcedLanguages'
    for name in list_values(path):remove(path,name)
    for name,value in state['chrome_languages'].items():put(path,name,value['value'],value['kind'])
    return {'restored':True,'browser_restart_required':True}

def status():
    result={label:{name:read(path,name).get('value') for name in VALUES[label]} for label,path in ROOTS.items()}
    result['Chrome']['ForcedLanguages']={name:value['value'] for name,value in list_values(ROOTS['Chrome']+r'\ForcedLanguages').items()}
    return {'policies_written':result,'browser_restart_required':True,'effective_policy_ui_verification_required':True}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('capture','apply','restore','status'));a=p.parse_args()
    result=globals()[a.action]()
    print(json.dumps(result,ensure_ascii=False))
    if a.action in ('apply','restore'):
        (FINAL/('browser-'+a.action+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
