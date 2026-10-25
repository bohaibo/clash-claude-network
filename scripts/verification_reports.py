"""Per-operation network evidence. Every generated report stays under .local/."""
import datetime,json,os,re,uuid
from client import LOCAL,write

def save_report(report,stage):
    if stage not in ('node-outage','core-exit','post-recovery','connectivity'):
        raise ValueError('Unknown verification stage')
    run_id=os.environ.get('CCN_VERIFY_RUN') or uuid.uuid4().hex
    if not re.fullmatch(r'[a-f0-9]{32}',run_id):raise ValueError('Invalid verification run ID')
    report=dict(report,run_id=run_id,stage=stage,time=datetime.datetime.now().astimezone().isoformat())
    folder=LOCAL/'verification-runs'/run_id
    write(folder/(stage+'.json'),report)
    # Read this run's stage files, never combine evidence from an earlier run.
    stages={path.stem:json.loads(path.read_text(encoding='utf-8')) for path in folder.glob('*.json')}
    summary=dict(report,stages=stages,success=all(value.get('success') is True for value in stages.values()))
    write(LOCAL/'verification.json',summary)
    return report
