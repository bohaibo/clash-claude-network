"""Per-operation network evidence. Every generated report stays under .local/."""
import datetime,json,os,re,uuid
from client import LOCAL,write

REQUIRED_STAGES={'node-outage','tun-off','core-exit','post-recovery','firewall-before','firewall-after'}

def coverage_for(stages):
    return 'full-network' if REQUIRED_STAGES <= stages.keys() else 'connectivity' if set(stages)=={'connectivity'} else 'partial'

def save_report(report,stage):
    if stage not in REQUIRED_STAGES | {'connectivity'}:
        raise ValueError('Unknown verification stage')
    run_id=os.environ.get('CCN_VERIFY_RUN') or uuid.uuid4().hex
    if not re.fullmatch(r'[a-f0-9]{32}',run_id):raise ValueError('Invalid verification run ID')
    from config_health import source_digest
    report=dict(report,evidence_kind=report.get('evidence_kind','network-test'),source_digest=source_digest(),run_id=run_id,stage=stage,time=datetime.datetime.now().astimezone().isoformat())
    folder=LOCAL/'verification-runs'/run_id
    write(folder/(stage+'.json'),report)
    # Read this run's stage files, never combine evidence from an earlier run.
    stages={path.stem:json.loads(path.read_text(encoding='utf-8')) for path in folder.glob('*.json')}
    base=stages.get('post-recovery',stages.get('connectivity',report))
    summary=dict(base,time=report['time'],coverage=coverage_for(stages),stages=stages,success=all(value.get('success') is True for value in stages.values()))
    write(LOCAL/'verification.json',summary)
    return report
