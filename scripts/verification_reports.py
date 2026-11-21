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
    from config_health import source_digest,configuration_revision
    folder=LOCAL/'verification-runs'/run_id
    preflight=folder/'firewall-before.json'
    revision = configuration_revision(LOCAL)
    expected_revision = report.get('configuration_revision', revision)
    # Native startup can rewrite the runtime YAML before TUN and firewall
    # readiness have been proven. Only compare to the completed preflight of
    # this run, while still honoring the revision captured by each network test.
    if stage != 'firewall-before' and preflight.is_file():
        first=json.loads(preflight.read_text(encoding='utf-8'))
        if first.get('success') is True and revision != first.get('configuration_revision'):
            expected_revision=first.get('configuration_revision')
    if revision != expected_revision:
        report = dict(report, success=False, failed_check='configuration_changed',
                      error='验收期间配置或规则已变化，请重新验收。')
        revision = None
    elif stage == 'firewall-before' and report.get('success') is True and not revision:
        report = dict(report, success=False, failed_check='configuration_unavailable',
                      error='无法确认当前配置版本，未开始故障演练。')
    if stage.startswith('firewall-') and report.get('failed_check') in {'configuration_changed','configuration_unavailable'}:
        report = dict(report, full_verified=False, status='unverified')
    report=dict(report,configuration_revision=revision,evidence_kind=report.get('evidence_kind','network-test'),source_digest=source_digest(),run_id=run_id,stage=stage,time=datetime.datetime.now().astimezone().isoformat())
    write(folder/(stage+'.json'),report)
    # Read this run's stage files, never combine evidence from an earlier run.
    stages={path.stem:json.loads(path.read_text(encoding='utf-8')) for path in folder.glob('*.json')}
    base=stages.get('post-recovery',stages.get('connectivity',report))
    summary=dict(base,time=report['time'],coverage=coverage_for(stages),stages=stages,success=all(value.get('success') is True for value in stages.values()))
    revisions={value.get('configuration_revision') for value in stages.values()}
    if len(revisions)!=1:
        summary['configuration_revision']=None
        summary['success']=False
    write(LOCAL/'verification.json',summary)
    return report
