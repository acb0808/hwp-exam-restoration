"""Install only this skill's restricted Antigravity reader, with a verified backup."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def run(backup, *, rollback=False, dry=False, agent_name='hwp-restoration-reader'):
    if agent_name not in ('hwp-restoration-reader','hwp-restoration-reviewer'): raise ValueError('unknown_agent')
    prefix='reader' if agent_name.endswith('-reader') else 'reviewer'
    source = Path(__file__).resolve().parents[1] / 'agents' / (agent_name+'.md')
    target = Path.home() / '.gemini' / 'config' / 'agents' / (agent_name+'.md')
    target = target.resolve(); backup = Path(backup).resolve()
    record = backup / (prefix+'-agent-installation.json')
    current = target.read_bytes() if target.is_file() else None
    if rollback:
        value = json.loads(record.read_text(encoding='utf-8'))
        if value['target'] != str(target) or digest(current) != value['after']:
            raise ValueError('reader_changed_since_install')
        previous = (backup/(prefix+'-agent-before.md')).read_bytes() if value['before'] else None
        if digest(previous) != value['before']: raise ValueError('reader_backup_changed')
        if not dry:
            if previous is None: target.unlink()
            else: atomic(target, previous)
        return {'status': 'rollback_ready' if dry else 'rolled_back', 'target': str(target)}
    if record.exists(): raise ValueError('choose_new_backup_directory')
    new = source.read_bytes()
    if dry: return {'status': 'ready', 'target': str(target), 'existing': current is not None}
    backup.mkdir(parents=True, exist_ok=True)
    if current is not None:
        with (backup/(prefix+'-agent-before.md')).open('xb') as stream: stream.write(current)
        if (backup/(prefix+'-agent-before.md')).read_bytes() != current: raise ValueError('reader_backup_failed')
    value = {'target': str(target), 'before': digest(current), 'after': digest(new), 'status': 'backed_up'}
    atomic(record, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
    if (target.read_bytes() if target.is_file() else None) != current: raise ValueError('reader_changed_during_install')
    atomic(target, new)
    if target.read_bytes() != new: raise ValueError('reader_install_verification_failed')
    value['status'] = 'installed'; atomic(record, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
    return {'status': 'installed', 'target': str(target), 'backup': str(record)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--rollback', action='store_true'); parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--agent-name',choices=['hwp-restoration-reader','hwp-restoration-reviewer'],default='hwp-restoration-reader')
    args = parser.parse_args()
    print(json.dumps(run(args.backup, rollback=args.rollback, dry=args.dry_run,agent_name=args.agent_name), ensure_ascii=False))
