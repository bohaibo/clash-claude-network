"""Runtime invariants remain enforced even when Python is started with -O."""

def require(condition,message='配置检查未通过，请查看本地记录。'):
    if not condition:
        raise AssertionError(message)
