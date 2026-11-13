"""Safe YAML codecs with optional LibYAML acceleration and portable fallback."""
import yaml

Loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
Dumper = getattr(yaml, 'CSafeDumper', yaml.SafeDumper)


def loads(value):
    return yaml.load(value, Loader=Loader)


def dumps(value, **options):
    return yaml.dump(value, Dumper=Dumper, **options)
