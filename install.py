"""Project-local Skill/Stop installer. Plan by default; trust is a separate step.

No credentials, model calls, global Skill installation, or Codex program changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parent/'skill/goal-await'
IGNORE_LINES = ['.goal-await/', '.agents/skills/goal-await/']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def atomic(path,value):
    write_bytes(path,(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def write_bytes(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if value is None:
        path.unlink(missing_ok=True)
        return
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(value)
    os.replace(tmp,path)


def change(path,data,changes):
    before=path.read_bytes() if path.exists() else None
    write_bytes(path,data)
    changes.append((path,before,digest(path)))


def rollback(changes):
    errors=[]
    for path,before,after in reversed(changes):
        try:
            if digest(path)!=after:
                raise ValueError('File changed concurrently; not overwritten')
            write_bytes(path,before)
        except Exception:
            errors.append(str(path))
    return errors


def remove_owned_tree(path,parent):
    resolved=path.resolve();parent=parent.resolve()
    if resolved==parent or not resolved.is_relative_to(parent) or path.is_symlink():
        raise ValueError('Unexpected recursive deletion target')
    shutil.rmtree(resolved)


def paths(project):
    project=Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Project must be an existing directory')
    skill=project/'.agents/skills/goal-await'
    hook=project/'.codex/hooks.json'
    record=project/'.goal-await/install.json'
    for target in [skill,hook,record,project/'.gitignore']:
        if not target.resolve().is_relative_to(project):
            raise ValueError('Installation paths must stay inside the selected project')
    return project,skill,hook,record


def definition(project,python,hook_shell=None):
    project,skill,hook,record=paths(project)
    args=[str(python),'-X','utf8',str(skill/'scripts/goal_await_stop.py'),
          '--bindings',str(project/'.goal-await/bindings'),'--max-continuations','3']
    return command_handler(args,hook_shell)


def command_handler(args,hook_shell=None):
    """Quote one native command consistently for project and user installs."""
    args=list(map(str,args))
    if os.name=='nt':
        hook_shell=hook_shell or 'powershell'
        if hook_shell not in ('powershell','cmd'):
            raise ValueError('Windows hook shell must be powershell or cmd')
        # The selected Windows shell receives this command literally. CMD uses
        # quoted arguments; PowerShell also needs its call operator and
        # escapes for characters that expand inside double-quoted arguments.
        if any(any(c in a for c in '\r\n"%!') for a in args):
            raise ValueError('Windows hook paths containing quotes, newlines, percent or exclamation marks are unsupported')
        quoted_args=args
        if hook_shell=='powershell':
            quoted_args=[a.replace('`','``').replace('$','`$') for a in args]
        cmd=' '.join('"'+a+'"' for a in quoted_args)
        if hook_shell=='powershell':
            cmd='& '+cmd
    else:
        cmd=shlex.join(args)
    return dict(type='command',command=cmd,timeout=10)


def plan(project,python,hook_shell=None):
    project,skill,hook,record=paths(project)
    return dict(project=str(project),skill=str(skill),hook_file=str(hook),
        handler=definition(project,python,hook_shell),data_directory=str(project/'.goal-await'),
        modifies_user_config=False,trust='review the exact definition with /hooks, or explicitly run tools/trust.py',
        installation_exists=record.exists())


def check_legacy_install(skill,record):
    """Prevent duplicate Stop hooks while an earlier installation is active."""
    if skill.exists() or skill.is_symlink():
        raise ValueError('Existing goal-lite Skill found; uninstall it with the old installer before installing goal-await')
    if record.exists() and not json.loads(record.read_text(encoding='utf-8')).get('uninstalled'):
        raise ValueError('Existing goal-lite installation recorded; uninstall it with the old installer before installing goal-await')


def install(project,python,backup_root,hook_shell=None):
    project,skill,hook,record=paths(project)
    check_legacy_install(project/'.agents/skills/goal-lite',project/'.goal-lite/install.json')
    if skill.exists() or (record.exists() and not json.loads(record.read_text(encoding='utf-8')).get('uninstalled')):
        raise ValueError('Existing installation/Skill found; inspect it and uninstall before reinstalling')
    backup_root=Path(backup_root).resolve()
    if backup_root.is_relative_to(project):
        raise ValueError('Keep configuration backups outside the project')
    original_hook=hook.read_bytes() if hook.exists() else None
    original_ignore=(project/'.gitignore').read_bytes() if (project/'.gitignore').exists() else None
    # Preflight encoding before any project mutation; never discover this after
    # writing the hook. Non-UTF8 files require explicit user conversion first.
    ignore=(original_ignore.decode('utf-8-sig') if original_ignore is not None else '')
    config=json.loads(original_hook.decode('utf-8-sig')) if original_hook is not None else {'hooks':{}}
    group=dict(hooks=[definition(project,python,hook_shell)])
    groups=config.setdefault('hooks',{}).setdefault('Stop',[])
    if not isinstance(groups,list):
        raise ValueError('Existing Stop hook groups must be a list')
    groups.append(group)
    backup=backup_root/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(parents=True,exist_ok=False)
    for name,data in [('hooks.json',original_hook),('gitignore',original_ignore)]:
        if data is not None:
            (backup/name).write_bytes(data)
    if record.exists():
        shutil.copyfile(record,backup/'previous-install.json')
    before_hook=digest(hook);before_ignore=digest(project/'.gitignore')
    stage=project/'.goal-await'/('stage-'+uuid.uuid4().hex)
    copied=[]
    changes=[];moved=False;skill_hashes={}
    try:
        for source in sorted(SOURCE.rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix!='.pyc':
                relative=source.relative_to(SOURCE)
                target=stage/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                text=source.read_text(encoding='utf-8').replace('{{PYTHON_JSON}}',json.dumps(python.as_posix(),ensure_ascii=False))
                text=text.replace('{{ENTRY_JSON}}',json.dumps((skill/'scripts/goal_entry.py').as_posix(),ensure_ascii=False))
                text=text.replace('{{SCOPE_JSON}}',json.dumps('project'))
                target.write_text(text,encoding='utf-8')
                copied.append(str(relative))
        atomic(stage/'installation.json',dict(workspace=str(project),python=str(python),schema_version=1))
        copied.append('installation.json')
        skill_hashes={p:digest(stage/p) for p in copied}
        if digest(hook)!=before_hook or digest(project/'.gitignore')!=before_ignore or skill.exists():
            raise ValueError('Project changed during installation; not overwritten')
        skill.parent.mkdir(parents=True,exist_ok=True)
        os.replace(stage,skill);moved=True
        change(hook,(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode('utf-8'),changes)
        additions=[line for line in IGNORE_LINES if line not in ignore.splitlines()]
        if additions:
            change(project/'.gitignore',(ignore+('' if not ignore or ignore.endswith('\n') else '\n')+
                                         '\n'.join(additions)+'\n').encode('utf-8'),changes)
        result=dict(schema_version=1,workspace=str(project),backup=str(backup),handler=group['hooks'][0],
            source_version='0.1.0-preview',hook_existed=original_hook is not None,ignore_existed=original_ignore is not None,
            before_hook_sha256=before_hook,before_ignore_sha256=before_ignore,
            installed_hook_sha256=digest(hook),installed_ignore_sha256=digest(project/'.gitignore'),
            ignore_additions=additions,skill_files=skill_hashes,trusted=False)
        change(record,(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'),changes)
    except Exception as exc:
        errors=rollback(changes)
        for candidate in [stage,skill] if moved else [stage]:
            try:
                if candidate.exists():
                    actual={str(p.relative_to(skill)) for p in skill.rglob('*') if p.is_file() and '__pycache__' not in p.parts} if candidate==skill else set()
                    if candidate==skill and (actual!=set(skill_hashes) or any(digest(skill/n)!=h for n,h in skill_hashes.items())):
                        raise ValueError('Skill changed; not removed')
                    remove_owned_tree(candidate,project)
            except Exception:
                errors.append(str(candidate))
        raise RuntimeError(f'Install failed: {exc}; backup={backup}; manual recovery paths={errors}') from None
    print(json.dumps(dict(installed=True,project=str(project),backup=str(backup),trust_required=True,
                          next_step='Review/trust this exact Stop definition once, then use a fresh or reloaded chat.'),ensure_ascii=False))


def uninstall(project,timers_paused=False):
    project,skill,hook,record=paths(project)
    meta=json.loads(record.read_text(encoding='utf-8'))
    # Abort before mutation if any goal still needs work or its timer may be active.
    for path in (project/'.goal-await/runs').glob('*/state.json'):
        state=json.loads(path.read_text(encoding='utf-8'))
        if state.get('state') in ['RUNNING','WAITING']:
            raise ValueError('Pause or finish active goals before uninstall')
        if state.get('automation_id') and not timers_paused:
            raise ValueError('Verify native timers are paused, then add --timers-paused; local state cannot confirm this')
    expected_files=set(meta['skill_files'])
    actual_files={str(p.relative_to(skill)) for p in skill.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual_files!=expected_files or any(digest(skill/name)!=sha for name,sha in meta['skill_files'].items()):
        raise ValueError('Installed Skill changed; preserve/review changes before uninstall')
    expected=(project/'.agents/skills/goal-await').resolve()
    if skill.resolve()!=expected or not expected.is_relative_to(project) or skill.is_symlink():
        raise ValueError('Unexpected Skill deletion target')
    config=json.loads(hook.read_text(encoding='utf-8-sig'))
    matches=[(group,handler) for group in config.get('hooks',{}).get('Stop',[]) for handler in group.get('hooks',[])
             if handler==meta['handler']]
    if len(matches)!=1:
        raise ValueError('Own hook changed or missing; inspect manually')
    # Only remove this handler. Preserve hooks added by the user after installation.
    group,handler=matches[0];group['hooks'].remove(handler)
    if not group['hooks']:
        config['hooks']['Stop'].remove(group)
    if not config['hooks']['Stop']:
        del config['hooks']['Stop']
    # Read recovery data before changing anything, then move the owned directory
    # aside on the same filesystem. A lock/permission failure leaves hooks intact.
    restore_ignore=digest(project/'.gitignore')==meta['installed_ignore_sha256']
    ignore_bytes=(Path(meta['backup'])/'gitignore').read_bytes() if restore_ignore and meta['ignore_existed'] else None
    stage=project/'.goal-await'/('uninstall-'+uuid.uuid4().hex)
    os.replace(skill,stage)
    changes=[]
    try:
        data=None if not meta['hook_existed'] and config=={'hooks':{}} else (json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        change(hook,data,changes)
        if restore_ignore:
            change(project/'.gitignore',ignore_bytes,changes)
        meta['uninstalled']=True
        change(record,(json.dumps(meta,ensure_ascii=False,indent=2)+'\n').encode('utf-8'),changes)
    except Exception as exc:
        errors=rollback(changes)
        try:os.replace(stage,skill)
        except Exception:errors.append(str(stage))
        raise RuntimeError(f'Uninstall failed: {exc}; manual recovery paths={errors}') from None
    retained_copy=None
    try:remove_owned_tree(stage,project/'.goal-await')
    except Exception:retained_copy=str(stage)
    print(json.dumps(dict(uninstalled=True,state_retained=True,retained_copy=retained_copy,
        trust='An inert trust entry may remain in user config. Remove only its exact key after reviewing it; no user config was overwritten.'),ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','install','inspect','uninstall'],nargs='?',default='plan')
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--python',type=Path,default=Path(sys.executable))
    p.add_argument('--backup-root',type=Path,default=Path.home()/'.codex/backups/goal-await')
    p.add_argument('--hook-shell',choices=['powershell','cmd'],
                   help='Windows Stop command shell (default: powershell)')
    p.add_argument('--timers-paused',action='store_true',help='Confirm native goal timers were checked and paused before uninstall')
    a=p.parse_args()
    python=a.python.resolve()
    if not python.is_file():
        p.error('Python executable does not exist')
    try:
        if a.action=='install':install(a.project,python,a.backup_root,a.hook_shell)
        elif a.action=='uninstall':uninstall(a.project,a.timers_paused)
        else:print(json.dumps(plan(a.project,python,a.hook_shell),ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps(dict(error=type(exc).__name__+': '+str(exc)),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':
    main()
