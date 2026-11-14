"""Opt-in native Stop hook for one explicitly bound Goal-await goal; stdlib only.

No registration, trust changes, model calls, timers, or cross-chat writes here.
Stop runs BEFORE turn/completed.
"""
import argparse
import json
from pathlib import Path
import sys

from goal_await import command, control_lock, load, save
from stop_evidence import assess


def review(root, state, kind, reason):
    state.update(state='NEEDS_REVIEW', before_pause='RUNNING', reason=reason)
    state['generation'] += 1
    save(root,state,kind,reason=reason)
    return {'systemMessage': 'Goal-await 自动接续已停止：' + reason + '；目标尚未验收。'}


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
        # State remains authoritative for pause, wait, delivery and completion.
        # Transcript activity only gates another nudge; it never proves success.
        if state['state'] != 'RUNNING':
            return {}
        if state.get('recovery_denied_turn_id') == event['turn_id']:
            return {}
        if state.get('delivery') and state['delivery']['state'] != 'ACKNOWLEDGED':
            return {'systemMessage': 'Goal-await 接续票据尚未确认，本轮不自动继续；请检查目标记录。'}
        turn_id = event['turn_id']
        previous = state.get('stop_hook', {})
        if previous.get('turn_id') == turn_id:
            # Waiting advances generation, but it does not create a new native
            # turn or make earlier tool activity new again.
            previous = dict(previous,generation=state['generation'])
        elif previous.get('generation') != state['generation']:
            previous = {}
        evidence = assess(event,state,previous)
        kind = evidence['kind']
        # An exact duplicate native callback must not spend another admission.
        if (kind not in ['unknown','interrupted'] and previous.get('turn_id') == turn_id and evidence.get('cursor') and
                previous.get('cursor') == evidence['cursor']):
            return {}
        record = dict(previous) if previous.get('turn_id') == turn_id else dict(
            turn_id=turn_id,generation=state['generation'],continuations=0)
        record.update(cursor=evidence.get('cursor'),last_kind=kind,
                      evidence_reason=evidence.get('reason'),activity_count=evidence.get('activity_count',0))
        state['stop_hook'] = record
        if kind == 'interrupted':
            state.update(state='PAUSED_BY_USER',before_pause='RUNNING',reason='用户中断，停止自动接续')
            state['generation'] += 1
            save(root,state,'stop_interrupted',turn_id=turn_id)
            return {}
        if kind in ['waiting','plan']:
            save(root,state,'stop_deferred',turn_id=turn_id,evidence_kind=kind)
            return {}
        if kind == 'unknown':
            return review(root,state,'stop_evidence_unavailable','无法核实本轮执行证据，请人工检查后恢复')
        if kind == 'no_activity' and (record['continuations'] or evidence.get('automatic') or event.get('stop_hook_active')):
            return review(root,state,'stop_no_activity','本段只有计划、状态或控制记账，没有新的业务工具活动')
        if kind not in ['activity','no_activity']:
            return review(root,state,'stop_evidence_unavailable','无法识别本轮执行证据，请人工检查后恢复')
        if record['continuations'] >= max_continuations:
            return review(root,state,'stop_hook_limit','本轮自动接续达到上限，请检查进展后恢复')
        record['continuations'] += 1
        state['stop_hook'] = record
        save(root, state, 'stop_hook_continued', turn_id=turn_id,
             count=record['continuations'],evidence_kind=kind,
             activity_count=record['activity_count'],stop_hook_active=bool(event.get('stop_hook_active')))
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
