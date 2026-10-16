"""Update this installed skill to the latest GitHub release, or only check for one. Standard library only.

    python scripts/update_skill.py            update in place (this copy of the skill only)
    python scripts/update_skill.py --check    report whether a newer release exists, change nothing
    python scripts/update_skill.py --rollback <backup folder>   put a backed-up copy back

The release comes only from github.com/acb0808/hwp-exam-restoration (never a URL from input). Its ZIP is checked
against the release's SHA256SUMS.txt and every file against the bundle manifest before the install is touched.
The install is backed up to ~/hwp-restoration-backups first; a failed copy puts the backup back. Only files the old
manifest lists and the new one does not are deleted: .mcp-venv and anything else is left alone. Python packages are
installed only when requirements-mcp.txt changed. On the Antigravity copy the six agent definitions are reinstalled
and checked byte for byte (a mismatch makes the MCP fall back to untyped subagents). Prints one JSON report."""
from pathlib import Path, PurePosixPath
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPO = 'acb0808/hwp-exam-restoration'
LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
SKILL = Path(__file__).resolve().parents[1]
BACKUPS = Path.home() / 'hwp-restoration-backups'
AGENTS_DIR = Path.home() / '.gemini' / 'config' / 'agents'
KEEP = {'.mcp-venv', '.venv', '__pycache__', '.pytest_cache'}
MANIFEST = 'BUNDLE-MANIFEST.json'
AGENTS = tuple(f'hwp-restoration-{role}{tier}' for role in ('reader', 'reviewer') for tier in ('', '-standard', '-light'))


def version_key(text):
    return tuple(int(p) for p in re.findall(r'\d+', str(text))[:4])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def installed(skill):
    """(version, {file: sha256}) of this copy. A copy without a manifest (a development checkout) lists no files."""
    path = skill / MANIFEST
    if path.is_file():
        value = json.loads(path.read_text(encoding='utf-8'))
        return value.get('version', '0'), dict(value.get('files', {}))
    found = re.search(r"RUNTIME_VERSION='([0-9.]+)'", (skill / 'scripts/restoration_single.py').read_text(encoding='utf-8'))
    return (found.group(1) if found else '0'), {}


def fetch(url, timeout=60):
    request = urllib.request.Request(url, headers={'User-Agent': 'hwp-exam-restoration-updater',
                                                   'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_release():
    """(tag, notes, zip bytes, SHA256SUMS text) of the latest release on GitHub."""
    release = json.loads(fetch(LATEST))
    assets = {a['name']: a['browser_download_url'] for a in release.get('assets', [])}
    zips = [n for n in assets if re.fullmatch(r'hwp-exam-restoration-[0-9.]+\.zip', n)]
    if len(zips) != 1 or 'SHA256SUMS.txt' not in assets: raise RuntimeError('release_assets_not_found')
    # Asset links point at github.com and its download host; nothing else is followed.
    for name in (zips[0], 'SHA256SUMS.txt'):
        if not assets[name].startswith(f'https://github.com/{REPO}/releases/download/'): raise RuntimeError('unexpected_asset_url')
    return release.get('tag_name', ''), release.get('body') or '', zips[0], fetch(assets[zips[0]], 300), fetch(assets['SHA256SUMS.txt']).decode('utf-8')


def checked_sum(sums, name, data):
    """The ZIP's hash must be the one SHA256SUMS.txt gives for its name (the file has CRLF line ends)."""
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip('*') == name:
            if parts[0].lower() != sha(data): raise RuntimeError('zip_hash_mismatch')
            return
    raise RuntimeError('zip_not_in_sha256sums')


def staged(zip_bytes, folder):
    """The skill folder of the release ZIP extracted into `folder`, every file checked against its manifest."""
    prefix = 'hwp-exam-restoration/'
    with zipfile.ZipFile(__import__('io').BytesIO(zip_bytes)) as archive:
        members = [n for n in archive.namelist() if n.startswith(prefix) and not n.endswith('/')]
        if prefix + MANIFEST not in members: raise RuntimeError('manifest_missing_in_zip')
        manifest = json.loads(archive.read(prefix + MANIFEST).decode('utf-8'))
        for name in members:
            rel = PurePosixPath(name[len(prefix):])
            if rel.is_absolute() or '..' in rel.parts: raise RuntimeError('unsafe_path_in_zip')
            data = archive.read(name)
            if str(rel) != MANIFEST and manifest['files'].get(str(rel)) != sha(data): raise RuntimeError('file_hash_mismatch: ' + str(rel))
            (folder / rel).parent.mkdir(parents=True, exist_ok=True); (folder / rel).write_bytes(data)
    listed = set(manifest['files']) | {MANIFEST}
    present = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    if listed != present: raise RuntimeError('zip_differs_from_manifest')
    return manifest


def kept(rel):
    return bool(set(PurePosixPath(rel).parts) & KEEP)


def back_up(skill, backups, version):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    target = backups / f'skill-update-{version}-{stamp}'
    files = sorted(p.relative_to(skill).as_posix() for p in skill.rglob('*') if p.is_file() and not kept(p.relative_to(skill).as_posix()))
    for rel in files:
        (target / 'skill' / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(skill / rel, target / 'skill' / rel)
    (target / 'record.json').write_text(json.dumps({'skill': str(skill), 'version': version, 'files': files}, ensure_ascii=False, indent=1), encoding='utf-8')
    return target


def restore(skill, backup, added=()):
    """Put the backed-up files back and remove files the update added. Folders such as .mcp-venv are not touched."""
    record = json.loads((backup / 'record.json').read_text(encoding='utf-8'))
    before = set(record['files'])
    version, files = installed(skill)
    for rel in (set(files) | set(added) | {MANIFEST}) - before:
        if (skill / rel).is_file() and not kept(rel): (skill / rel).unlink()
    for rel in sorted(before):
        (skill / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(backup / 'skill' / rel, skill / rel)
    return record['version']


def apply(skill, stage, new_files, old_files):
    for rel in set(old_files) - set(new_files):
        if (skill / rel).is_file() and not kept(rel): (skill / rel).unlink()
    for rel in sorted(set(new_files) | {MANIFEST}):
        (skill / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(stage / rel, skill / rel)
    for rel in new_files:
        if sha((skill / rel).read_bytes()) != new_files[rel]: raise RuntimeError('copy_verification_failed: ' + rel)
    return len(set(old_files) - set(new_files)), len(new_files)


def packages(skill, old_requirements):
    """pip into the skill's own .mcp-venv, only when requirements-mcp.txt changed. Optional packages may fail."""
    python = skill / '.mcp-venv' / 'Scripts' / 'python.exe'
    if not python.is_file(): python = skill / '.mcp-venv' / 'bin' / 'python'
    new = (skill / 'requirements-mcp.txt').read_bytes() if (skill / 'requirements-mcp.txt').is_file() else b''
    if new == old_requirements: return {'status': 'unchanged'}
    if not python.is_file(): return {'status': 'no_venv', 'note': 'requirements-mcp.txt changed but this copy has no .mcp-venv'}
    result = {}
    for label, name, required in (('required', 'requirements-mcp.txt', True), ('optional', 'requirements-optional.txt', False)):
        if not (skill / name).is_file(): continue
        run = subprocess.run([str(python), '-m', 'pip', 'install', '-q', '-r', str(skill / name)], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=1800)
        result[label] = 'ok' if run.returncode == 0 else 'failed: ' + (run.stderr or run.stdout).strip()[-400:]
    result['status'] = 'ok' if result.get('required', 'ok') == 'ok' else 'failed'
    return result


def antigravity_copy(skill):
    home = Path.home()
    return any(skill.is_relative_to(home / p) for p in ('.agents/skills', '.gemini/config/skills'))


def agents(skill, backup, agents_dir):
    """Reinstall the agent definitions of the Antigravity copy and check each one equals the skill's file."""
    out = {}
    for name in AGENTS:
        source = skill / 'agents' / f'{name}.md'
        if not source.is_file(): continue
        run = subprocess.run([sys.executable, str(skill / 'scripts' / 'install_reader_agent.py'), '--backup', str(backup / 'agents'),
                              '--agent-name', name], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        target = agents_dir / f'{name}.md'
        same = target.is_file() and target.read_bytes() == source.read_bytes()
        out[name] = 'installed' if run.returncode == 0 and same else 'mismatch: ' + (run.stderr or run.stdout).strip()[-200:]
    return out


def main(argv=None, *, skill=SKILL, backups=BACKUPS, agents_dir=AGENTS_DIR, release=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='report only')
    parser.add_argument('--rollback', type=Path, help='backup folder printed by an earlier update')
    parser.add_argument('--zip', type=Path, help=argparse.SUPPRESS); parser.add_argument('--sums', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    skill = Path(skill).resolve()
    if args.rollback:
        version = restore(skill, args.rollback.resolve())
        if antigravity_copy(skill) and (args.rollback / 'agents').is_dir():
            for name in AGENTS:
                subprocess.run([sys.executable, str(skill / 'scripts' / 'install_reader_agent.py'), '--backup', str(args.rollback / 'agents'),
                                '--agent-name', name, '--rollback'], capture_output=True)
        return {'status': 'rolled_back', 'version': version, 'next_steps': ['앱에서 hwp-restoration MCP를 재연결하거나 앱을 다시 시작하세요.']}
    old_version, old_files = installed(skill)
    if release is None:
        if args.zip:
            release = ('local', '', args.zip.name, args.zip.read_bytes(), args.sums.read_text(encoding='utf-8'))
        else:
            release = latest_release()
    tag, notes, zip_name, zip_bytes, sums = release
    checked_sum(sums, zip_name, zip_bytes)
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / 'skill'
        manifest = staged(zip_bytes, stage)
        new_version, new_files = manifest['version'], manifest['files']
        report = {'from': old_version, 'to': new_version, 'release': tag, 'skill': str(skill), 'release_notes': notes[:3000]}
        if new_files == old_files: return report | {'status': 'up_to_date'}
        if version_key(new_version) < version_key(old_version): return report | {'status': 'newer_installed'}
        if args.check: return report | {'status': 'update_available'}
        old_requirements = (skill / 'requirements-mcp.txt').read_bytes() if (skill / 'requirements-mcp.txt').is_file() else b''
        backup = back_up(skill, Path(backups), old_version)
        try:
            removed, copied = apply(skill, stage, new_files, old_files)
        except Exception as exc:
            restore(skill, backup, new_files)
            return report | {'status': 'failed', 'error': str(exc), 'backup': str(backup),
                             'next_steps': ['업데이트를 끝내지 못해 원래 파일로 되돌렸습니다. 앱을 닫은 뒤 다시 실행해 보세요.']}
    report |= {'status': 'updated', 'backup': str(backup), 'removed': removed, 'files': copied,
               'packages': packages(skill, old_requirements),
               'rollback': f'python "{skill / "scripts" / "update_skill.py"}" --rollback "{backup}"'}
    if antigravity_copy(skill): report['agents'] = agents(skill, backup, Path(agents_dir))
    steps = ['앱에서 hwp-restoration MCP를 재연결하거나 앱을 다시 시작하세요. 그래야 새 코드가 실행됩니다.',
             f'다음 복원의 첫 응답에서 runtime_version이 {new_version}인지 확인합니다.']
    if report['packages'].get('status') == 'failed':
        report['status'] = 'updated_packages_failed'
        steps.insert(0, '필수 Python 패키지 설치가 실패했습니다. 앱을 완전히 닫은 뒤 이 업데이트를 다시 실행하세요.')
    if any(v != 'installed' for v in report.get('agents', {}).values()):
        report['status'] = 'updated_agents_mismatch'
        steps.insert(0, '서브에이전트 정의가 스킬과 다릅니다. 이대로면 제작자가 일반 에이전트로 실행됩니다. 업데이트를 다시 실행하세요.')
    report['next_steps'] = steps
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=1))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                          'next_steps': ['설치된 스킬은 바뀌지 않았습니다. 인터넷 연결을 확인하고 다시 실행하세요.']}, ensure_ascii=False, indent=1))
        sys.exit(1)
