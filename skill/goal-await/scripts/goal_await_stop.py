"""Opt-in native Stop hook for one explicitly bound Goal-await goal; stdlib only.

No registration, trust changes, model calls, timers, or cross-chat writes here.
Stop runs BEFORE turn/completed.
"""
import argparse
import json
from pathlib import Path
import sys

from goal_await import command, control_lock, load, save


def stop(root, event, max_continuations=3):
    if max_continuations < 1:
        raise ValueError('max-continuations must be positive')
    if event.get('hook_event_name') != 'Stop':
        return {}
    root = Path(root).resolve()
    with control_lock(root):
        state = load(root)
        if (event.get('session_id') != state['thread_id'] or not event.get('turn_id')
                or not event.get('cwd')
                # Native Windows cwd may use the \\?\ extended-path prefix.
                # Compare directory identity, not the spelling of its path.
                or not Path(event['cwd']).samefile(state['workspace'])):
            return {}
        # This is a working-state gate, not a task-specific completion checker.
        # Waiting is owned by the existing native timer; manual pause always wins.
        if state['state'] != 'RUNNING':
            return {}
        if state.get('delivery') and state['delivery']['state'] != 'ACKNOWLEDGED':
            return {'systemMessage': 'Goal-await 接续票据尚未确认，本轮不自动继续；请检查目标记录。'}
        turn_id = event['turn_id']
        record = state.get('stop_hook', {})
        if record.get('turn_id') != turn_id or record.get('generation') != state['generation']:
            record = dict(turn_id=turn_id, generation=state['generation'], continuations=0)
        if record['continuations'] >= max_continuations:
            state.update(state='NEEDS_REVIEW', before_pause='RUNNING',
                         reason='本轮自动接续达到上限，目标仍未完成；请检查进展后手动恢复')
            state['generation'] += 1
            save(root, state, 'stop_hook_limit', turn_id=turn_id)
            return {'systemMessage': 'Goal-await 已暂停：本轮自动接续达到上限，目标尚未完成。'}
        record['continuations'] += 1
        state['stop_hook'] = record
        save(root, state, 'stop_hook_continued', turn_id=turn_id,
             count=record['continuations'], stop_hook_active=bool(event.get('stop_hook_active')))
        return {'decision': 'block', 'reason': (
            f"Goal-await 目标仍为 RUNNING，请在当前对话继续原授权目标：{state['objective']}\n"
            f"先读取最新上下文：{command(root, 'context')}\n"
            '不要把单次回复当作目标完成。按真实证据推进下一步；需要等待时登记 wait 并同步返回的原生定时操作，'
            '确需用户输入或授权时 blocked，完整目标验收后 complete；不得扩大授权或重做已完成工作。\n'
            f"状态命令入口：{command(root, 'status')}；将 status 替换为相应动作并提供必要参数。")}


def stop_current(bindings,event,max_continuations=3):
    """Resolve only this chat's explicit binding; never scan previous goals."""
    if event.get('hook_event_name')!='Stop':
        return {}
    from goal_entry import current
    root=current(event.get('session_id'),bindings)
    return stop(root,event,max_continuations) if root else {}


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    target=parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--run',type=Path)
    target.add_argument('--bindings',type=Path)
    parser.add_argument('--max-continuations', type=int, default=3)
    args = parser.parse_args()
    try:
        event=json.load(sys.stdin)
        output = (stop_current(args.bindings,event,args.max_continuations) if args.bindings
                  else stop(args.run,event,args.max_continuations))
        print(json.dumps(output, ensure_ascii=False))
    except Exception:
        # Hook failures must be visible, without dumping arbitrary input/context.
        print(json.dumps({'systemMessage': 'Goal-await Stop hook 执行失败，未自动继续；请检查绑定记录和本地脚本。'}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
