"""Optional one-definition trust via native App Server. No model or thread calls."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import tomllib
import uuid

from rpc import AppServer


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True,type=Path)
    p.add_argument('--codex',help='Exact Codex executable; otherwise resolve codex from PATH')
    p.add_argument('--apply',action='store_true',help='Actually trust the displayed exact definition; default is read-only')
    p.add_argument('--backup-root',type=Path,default=Path.home()/'.codex/backups/goal-await-trust')
    args=p.parse_args()
    project=args.project.resolve()
    meta_path=project/'.goal-await/install.json'
    meta=json.loads(meta_path.read_text(encoding='utf-8'))
    if meta.get('uninstalled') or Path(meta['workspace']).resolve()!=project:
        raise ValueError('Expected an active installation for this project')
    binary=Path(args.codex or shutil.which('codex') or '').resolve()
    if not binary.is_file():
        raise ValueError('Use --codex to select an existing Codex executable')
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).resolve()
    user=home/'config.toml'
    backup_root=args.backup_root.resolve()
    if backup_root.is_relative_to(project):
        raise ValueError('Keep user configuration backups outside the project')
    # Catalog/config RPC only. Never resume, start or send to any conversation.
    with AppServer(binary,project) as app:
        rows=app.call('hooks/list',dict(cwds=[str(project)]))['data'][0]
        if rows.get('errors'):
            raise ValueError('Hook discovery errors; no trust changes')
        own=[h for h in rows['hooks'] if h.get('command')==meta['handler']['command'] and
             Path(h['sourcePath']).resolve()==(project/'.codex/hooks.json').resolve() and h.get('eventName')=='stop']
        if len(own)!=1:
            raise ValueError('Expected exactly this installation\'s project Stop definition')
        hook=own[0]
        print(json.dumps({k:hook.get(k) for k in ['key','command','currentHash','trustStatus','enabled']},ensure_ascii=False))
        if not args.apply:
            return
        if not hook['enabled']:
            raise ValueError('Hook is disabled; review configuration rather than silently enabling it')
        raw=app.call('config/read',dict(includeLayers=True,cwd=str(project)))
        layer=next(x for x in raw['layers'] if x['name']['type']=='user' and
                   Path(x['name']['file']).resolve()==user and not x['name'].get('profile'))
        before_bytes=user.read_bytes() if user.exists() else None
        before=tomllib.loads(before_bytes.decode('utf-8-sig')) if before_bytes else {}
        backup=backup_root/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True,exist_ok=False)
        if before_bytes is not None:
            (backup/'config.toml').write_bytes(before_bytes)
        old=copy.deepcopy(before.get('hooks',{}).get('state',{}).get(hook['key']))
        expected=copy.deepcopy(before)
        expected.setdefault('hooks',{}).setdefault('state',{}).setdefault(hook['key'],{})['trusted_hash']=hook['currentHash']
        app.call('config/batchWrite',dict(filePath=str(user),expectedVersion=layer['version'],
            edits=[dict(keyPath='hooks.state',value={hook['key']:{'trusted_hash':hook['currentHash']}},mergeStrategy='upsert')],
            reloadUserConfig=True))
        after=tomllib.loads(user.read_text(encoding='utf-8-sig'))
        if after!=expected:
            raise ValueError('Unexpected concurrent config change; preserve backups and inspect; no automatic overwrite')
        meta.update(trusted=True,trust_key=hook['key'],trusted_hash=hook['currentHash'],
                    previous_trust=old,user_config_path=str(user),trust_backup=str(backup),user_config_after_sha256=sha(user))
        tmp=meta_path.with_suffix('.tmp');tmp.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,meta_path)
        print(json.dumps(dict(trusted=True,backup=str(backup),desktop_reload_required=True),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps(dict(error=type(exc).__name__+': '+str(exc)),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
