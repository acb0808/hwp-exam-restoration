"""Register this local stdio server, preserving and backing up other servers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

NAME='hwp-restoration'
def sha(data): return hashlib.sha256(data).hexdigest()
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path.home()/'.gemini/config/mcp_config.json')
    parser.add_argument('--python',type=Path)
    parser.add_argument('--engine',type=Path)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--rollback',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    a=parser.parse_args(); config=a.config.resolve(); backup=a.backup.resolve()
    receipt=backup/'mcp-installation.json'; raw=backup/'mcp_config.before.json'
    current=config.read_bytes() if config.exists() else None
    if a.rollback:
        record=json.loads(receipt.read_text(encoding='utf-8'))
        if record['config']!=str(config) or current is None or sha(current)!=record['after']:
            raise ValueError('config_changed_since_install_do_not_overwrite')
        previous=raw.read_bytes() if record['before'] is not None else None
        if previous is not None and sha(previous)!=record['before']: raise ValueError('backup_hash_mismatch')
        if not a.dry_run:
            if previous is None: config.unlink()
            else: atomic(config,previous)
        print(json.dumps({'status':'rollback_ready' if a.dry_run else 'rolled_back','server':NAME})); return
    if receipt.exists(): raise ValueError('choose_new_backup_receipt_directory')
    python=(a.python or Path(__file__).resolve().parents[1]/'.mcp-venv/Scripts/python.exe').resolve(strict=True)
    script=Path(__file__).with_name('restoration_mcp.py').resolve(strict=True)
    value=json.loads(current.decode('utf-8-sig')) if current else {}
    servers=value.setdefault('mcpServers',{})
    if not isinstance(servers,dict): raise ValueError('mcpServers_object_required')
    entry={'command':str(python),'args':['-B','-X','utf8',str(script)],'cwd':str(script.parent),'env':{'PYTHONUTF8':'1'}}
    if a.engine: entry['env']['HWP_TIKZ_ENGINE']=str(a.engine.resolve(strict=True))
    others={k:v for k,v in servers.items() if k!=NAME}
    servers[NAME]=entry
    new=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
    assert {k:v for k,v in json.loads(new)['mcpServers'].items() if k!=NAME}==others
    if a.dry_run:
        print(json.dumps({'status':'ready','server':NAME,'other_servers_preserved':len(others)}));return
    backup.mkdir(parents=True,exist_ok=True)
    if current is not None:
        if raw.exists(): raise ValueError('backup_file_already_exists')
        raw.write_bytes(current)
        if raw.read_bytes()!=current: raise ValueError('backup_verification_failed')
    if (config.read_bytes() if config.exists() else None)!=current: raise ValueError('config_changed_during_install')
    atomic(config,new)
    if config.read_bytes()!=new: raise ValueError('config_write_verification_failed')
    atomic(receipt,json.dumps({'config':str(config),'before':sha(current) if current else None,'after':sha(new),
        'server':NAME,'other_servers_preserved':len(others)},indent=2).encode())
    print(json.dumps({'status':'registered','server':NAME,'other_servers_preserved':len(others),'restart_or_refresh_required':True}))

if __name__=='__main__': main()
