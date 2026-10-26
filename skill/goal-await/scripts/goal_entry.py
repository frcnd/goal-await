"""Small desktop entry: current chat -> current Goal-await record, stdlib only."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

import goal_await
from common import atomic_json, exclusive

from settings import WORKSPACE, DATA, SCOPE
BINDINGS = DATA / 'bindings'
ACTIONS = {'wait','pause','resume','complete','blocked','status','context','context-update','bind','effects'}


def binding_path(thread_id, bindings=BINDINGS):
    if not isinstance(thread_id,str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',thread_id):
        raise ValueError('A valid current CODEX_THREAD_ID is required')
    return Path(bindings)/(thread_id.lower()+'.json')


def current(thread_id, bindings=BINDINGS):
    path=binding_path(thread_id,bindings)
    if not path.exists():
        return None
    binding=json.loads(path.read_text(encoding='utf-8'))
    root=goal_await.resolve_run_id(binding['run_id'])
    state=goal_await.load(root)
    if state['thread_id']!=thread_id or binding['thread_id']!=thread_id:
        raise ValueError('Current goal belongs to a different chat')
    return root


def start(objective,thread_id,*,workspace=None,context_summary='',project_id='local',bindings=BINDINGS):
    if not objective or not objective.strip():
        raise ValueError('A concrete objective is required')
    workspace=Path(workspace or Path.cwd()).resolve()
    if not workspace.is_dir():
        raise ValueError('Workspace must be an existing directory')
    if SCOPE == 'project' and not workspace.is_relative_to(WORKSPACE.resolve()):
        raise ValueError('This installation is scoped to this project workspace')
    if SCOPE == 'user':
        if not workspace.samefile(Path.cwd()):
            raise ValueError('Use this chat current working directory as workspace')
        # Project and user hooks are additive. Never silently create a second
        # independent goal behind the same Skill name in a project.
        for parent in [workspace, *workspace.parents]:
            for name in ('goal-await', 'goal-lite'):
                local_skill=parent/'.agents/skills'/name
                if local_skill.exists() and local_skill.resolve()!=Path(__file__).resolve().parents[1]:
                    raise ValueError(f'Project {name} Skill also exists; finish its goals and uninstall it before using user scope')
            if (parent/'.git').exists():
                break
    path=binding_path(thread_id,bindings)
    path.parent.mkdir(parents=True,exist_ok=True)
    with exclusive(path.with_suffix('.lock')):
        previous=current(thread_id,bindings)
        if previous and goal_await.load(previous)['state']!='DONE':
            raise ValueError('This chat already has an unfinished goal; resume or finish it first')
        root=goal_await.create(objective.strip(),thread_id,project_id,workspace=workspace,
                              context_summary=context_summary)
        atomic_json(path,dict(thread_id=thread_id,run_id=root.name))
    return dict(accepted=True,run=str(root),run_id=root.name,state='RUNNING',
                binding=str(path),effects={},next_step='继续完整目标；需要等待时调用 wait；完整验收后 complete。')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        action=sys.argv[1] if len(sys.argv)>1 else 'status'
        thread_id=os.environ.get('CODEX_THREAD_ID')
        if action=='start':
            p=argparse.ArgumentParser(description=__doc__)
            p.add_argument('--objective',required=True)
            p.add_argument('--workspace',type=Path)
            p.add_argument('--context-summary',default='')
            p.add_argument('--project-id',default='local')
            args=vars(p.parse_args(sys.argv[2:]))
            print(json.dumps(start(thread_id=thread_id,**args),ensure_ascii=False,indent=2))
            return
        if action not in ACTIONS:
            raise ValueError('Use start, wait, pause, resume, complete, blocked, status, context, context-update, bind or effects')
        # Targets come from the current conversation, never from typed chat/run IDs.
        if any(a.split('=')[0] in ['--run','--run-id','--thread-id'] for a in sys.argv[2:]):
            raise ValueError('This entry automatically selects the current chat goal')
        root=current(thread_id)
        if root is None:
            if action=='status':
                print(json.dumps(dict(active=False,thread_id=thread_id)))
                return
            raise ValueError('No current goal; call start first')
        sys.argv=[str(Path(goal_await.__file__).resolve()),action,'--run-id',root.name,*sys.argv[2:]]
        goal_await.main()
    except Exception as exc:
        print(json.dumps(dict(error=type(exc).__name__+': '+str(exc)),ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':
    main()
