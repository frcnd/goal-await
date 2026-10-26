"""Project-local Goal-await state and native-app action adapter. Python stdlib only.

No timer daemon, model client, credentials, or user configuration writes here.
The calling Codex chat executes returned actions with its existing native tools.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

from common import atomic_json, exclusive, log_event, wake_once

ROOT = Path(__file__).resolve().parent
from settings import WORKSPACE, DATA
RUNS = DATA / 'runs'
PYTHON = Path(sys.executable)


def ps(value):
    return ("'" + str(value).replace("'", "''") + "'") if os.name == 'nt' else shlex.quote(str(value))


def command(root, action):
    root = Path(root).resolve()
    target = f'--run-id {ps(root.name)}' if root.parent == RUNS.resolve() else f'--run {ps(root)}'
    prefix = '& ' if os.name == 'nt' else ''
    return f'{prefix}{ps(PYTHON)} -X utf8 {ps(Path(__file__).resolve())} {action} {target}'


def resolve_run_id(run_id):
    """Resolve an identifier in this project's existing goal directory only."""
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', run_id):
        raise ValueError('run-id must be a plain goal identifier, not a path')
    root = (RUNS / run_id).resolve()
    if root.parent != RUNS.resolve():
        raise ValueError('run-id resolved outside this project')
    return root


def load(root):
    return json.loads((Path(root) / 'state.json').read_text(encoding='utf-8'))


def save(root, state, kind, **detail):
    state['updated_at'] = time.time()
    atomic_json(Path(root) / 'state.json', state)
    atomic_json(Path(root) / 'context.json', dict(objective=state['objective'],
        workspace=state.get('workspace', str(WORKSPACE)), summary=state.get('context_summary', ''),
        thread_id=state['thread_id'], generation=state['generation'], wait=state.get('wait'),
        last_check=state.get('last_check'), last_decision=state.get('last_decision')))
    log_event(root, kind, **detail)


@contextmanager
def control_lock(root):
    # A send receipt can arrive at the same time as the original chat's ACK.
    # Wait briefly for that local file update; never replay a model call/message.
    deadline = time.monotonic() + 3
    while True:
        guard = exclusive(Path(root) / 'control.lock')
        try:
            guard.__enter__()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)
    try:
        yield
    finally:
        guard.__exit__(None, None, None)


def create(objective, thread_id, project_id, *, checker_thread_id=None, root=None, model=None,
           continuation='继续原目标，先读取检查结果再行动。', observer_mode='same-chat',
           workspace=None, context_summary=''):
    if observer_mode != 'same-chat':
        raise ValueError('This preview supports same-chat mode only')
    if observer_mode == 'same-chat':
        if checker_thread_id and checker_thread_id != thread_id:
            raise ValueError('Use observer-mode independent for a separate checker')
        checker_thread_id = thread_id
    elif not checker_thread_id or checker_thread_id == thread_id:
        raise ValueError('Independent mode requires a separate, verified checker chat')
    root = Path(root) if root else RUNS / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=False)
    state = dict(run_id=root.name, objective=objective, thread_id=thread_id, project_id=project_id,
                 state='RUNNING', generation=0, wake_count=0, wait=None, automation_id=None,
                 checker_model=model, checker_thread_id=checker_thread_id, trigger_kind='heartbeat',
                 observer_mode=observer_mode, workspace=str(Path(workspace or Path.cwd()).resolve()),
                 context_summary=context_summary,
                 continuation=continuation, delivery=None, created_at=time.time())
    save(root, state, 'goal_started', thread_id=thread_id)
    return root


def enter_wait(state, note):
    state['generation'] += 1
    state['state'] = 'WAITING'
    state['wait'] = dict(note, run_id=state['run_id'], thread_id=state['thread_id'],
                         generation=state['generation'], created_at=time.time(), checks=0)
    state['delivery'] = None
    state.pop('active_observation', None)
    state.pop('reason', None)


def checker_prompt(root, state):
    return (f"Goal-await {state['run_id']}：本轮只执行下列准入命令，不改模型，不执行历史等待命令。\n"
            f"{command(root, 'check')} --generation {state['generation']}\n"
            "读取 JSON 的 context、instructions 和 observation_id；获准后自主只读调查，脚本仅为可选证据。"
            "准入失败则执行返回的暂停操作并结束。正常未完成保持安静，只报告实质变化、完成、失败或需要用户行动。")


def observation_instructions(root, state):
    route = ('当前对话本身就是业务主对话。严禁调用 send_message_to_thread、claim、sent 或 delivery-error；'
             '就绪/失败后只同步返回的暂停操作，在本对话 acknowledge，再执行 local_continuation。' if state.get('observer_mode') == 'same-chat'
             else '你是独立观察者；仅在返回 continuation 时向指定原主对话发送一次消息。')
    return f'''{route}
你在执行有上下文的状态调查，而非转发脚本判决。context 是目标摘要，不会扩大人类授权。
根据目标、完成标准与真实证据，自主选择已授权的文件、日志、进程、远端查询或现有脚本；无需专用检查器。
检查指南和旧脚本输出仅供参考。工具失败先核对返回路径、工作目录和参数，并尝试其他已授权只读证据；保留原错误，不提权、不重试外部模型、不重启/重提/取消业务工作，不修改业务产物。
任务状态 task_state 为 running/completed/failed/uncertain；观测状态 observation_state 为 ok/tool_error/insufficient_evidence。工具或路径出错、进程查询被拒绝均不证明任务失败，也不自动需要用户介入。
脚本故障但替代证据充分时，可以独立判断完成或失败；有真实业务失败证据则报告 failed，交回主对话决定修复或下一步。确有授权不足、必需用户输入或安全边界才可 blocked，并提供 blocker_kind=authorization/input/safety/user。长时间不确定仍是 uncertain，达到观察预算后暂停观察并提示，不能谎报业务 blocked。
报告必须绑定本轮 observation_id，并写明真实证据来源。若报告因编号不匹配被拒绝，核对本轮准入回执并更正字段，不把控制记账错误当作业务问题，也不重新执行check消耗新一轮：
{command(root, 'report')} --generation {state['generation']} --observation-id '<本轮ID>' --task-state <状态> --observation-state <状态> --reason '<简短理由>' --evidence '<实际证据与工具错误>'
若确需用户处理，可改用 --result blocked 并添加 --blocker-kind <类型>。理由和证据正确转义，不执行占位符。
读取返回 JSON。宿主有 functions.exec 时，可执行 execute_effects 代码透传参数；没有该编排工具时按 effects 调用现有原生工具，必须保留 id 和短入口，不摘要或改写 prompt。只能执行实际返回的分支，不能从历史消息推断另一个发送步骤。只管理本目标的定时任务及经授权的原主对话，不覆盖模型。若为 local_continuation，仅在当前业务对话执行 acknowledge_command 后按其 prompt 继续，严禁发消息给自己。
控制记账写入属于本任务操作；业务材料保持只读。每次触发只调查一次。未完成且无实质变化时保持安静；工具或发送失败明确记录，不自动重发。
'''


def dispatch_code(root, state):
    # Native app APIs remain in the host. Fetch JSON mechanically; never retype its prompt.
    cmd = command(root, 'effects')
    if state.get('observer_mode') == 'same-chat':
        return f'''const r = await tools.exec_command({{cmd: {json.dumps(cmd)}}});
if (r.exit_code !== 0) throw new Error(r.output);
const p = JSON.parse(r.output);
if (p.effects.automation) {{ const a = await tools.mcp__codex_app__automation_update(p.effects.automation); if (a.isError) throw new Error("automation_update failed"); }}
if (p.effects.local_continuation) text(p.effects.local_continuation);
'''
    return f'''const r = await tools.exec_command({{cmd: {json.dumps(cmd)}}});
if (r.exit_code !== 0) throw new Error(r.output);
const p = JSON.parse(r.output);
if (p.effects.automation) {{ const a = await tools.mcp__codex_app__automation_update(p.effects.automation); if (a.isError) throw new Error("automation_update failed"); }}
if (p.effects.continuation) {{
  const ticket = p.effects.ticket;
  const c = await tools.exec_command({{cmd: {json.dumps(command(root, 'claim'))} + " --ticket '" + ticket + "'"}});
  if (c.exit_code !== 0) throw new Error(c.output);
  if (JSON.parse(c.output).accepted) {{
    try {{
      const s = await tools.mcp__codex_app__send_message_to_thread(p.effects.continuation);
      if (s.isError) throw new Error("send_message_to_thread failed");
      const receipt = await tools.exec_command({{cmd: {json.dumps(command(root, 'sent'))} + " --ticket '" + ticket + "'"}});
      if (receipt.exit_code !== 0) throw new Error(receipt.output);
    }} catch (e) {{
      await tools.exec_command({{cmd: {json.dumps(command(root, 'delivery-error'))} + " --ticket '" + ticket + "' --reason 'Native delivery failed or uncertain; inspect tool trace; no resend'"}});
      throw e;
    }}
  }}
}}
if (p.effects.local_continuation) text(p.effects.local_continuation);
'''


def on_turn_completed(root, event):
    """Adapter for an owning private App Server client, NOT a Desktop subscription."""
    if event.get('method') != 'turn/completed':
        return dict(accepted=False, effects={})
    params = event['params']
    return act(root, 'turn-ended', thread_id=params['threadId'], turn_id=params['turn']['id'],
               turn_status=params['turn']['status'])


def effects(root, state, *, dispatch=False):
    result = {}
    ident = state.get('automation_id')
    if state.get('wait') and (ident or state['state'] == 'WAITING'):
        spec = dict(name='Goal-await ' + state['run_id'],
                    prompt=checker_prompt(root, state),
                    rrule=f"RRULE:FREQ=MINUTELY;INTERVAL={state['wait']['interval_minutes']}",
                    status='ACTIVE' if state['state'] == 'WAITING' else 'PAUSED')
        if state.get('trigger_kind', 'cron') == 'heartbeat':
            spec.update(kind='heartbeat', destination='thread', targetThreadId=state['checker_thread_id'])
        else:  # Preserve existing records; new goals always use a fixed checker chat.
            spec.update(kind='cron', destination='local', executionEnvironment='local',
                        projectId=state['project_id'], model=state['checker_model'], reasoningEffort='low')
        result['automation'] = dict(spec, mode='update', id=ident) if ident else dict(spec, mode='create')
    delivery = state.get('delivery')
    if dispatch and delivery and delivery['state'] == 'PENDING' and state['state'] == 'RUNNING':
        result['ticket'] = delivery['ticket']
        observation = state.get('last_check', {})
        message = (
            f"Goal-await 需要继续原目标（{state['run_id']}，事件={delivery.get('cause', 'ready')}）。\n"
            f"完整目标：{state['objective']}\n"
            f"检查结果：{observation.get('reason', '本轮结束，目标尚未完成')}\n证据：{observation.get('evidence', '')}\n"
            f"先运行 {command(root, 'acknowledge')} --ticket {ps(delivery['ticket'])}。"
            "若 accepted 不是 true，说明任务已暂停或本次继续已失效，停止本次接续。\n"
            "真实业务失败是需要分析的结果，不是目标已完成。调查原因，在已有授权内决定修复、调整、继续等待或需要用户处理；不自动重复提交。\n"
            + state['continuation'])
        if state.get('observer_mode') == 'same-chat':
            result['local_continuation'] = dict(ticket=delivery['ticket'], prompt=message,
                acknowledge_command=command(root, 'acknowledge') + ' --ticket ' + ps(delivery['ticket']))
        else:
            result['continuation'] = dict(threadId=state['thread_id'], hostId='local', prompt=message)
    return result


def response(root, state, *, accepted=True, dispatch=False, include_effects=True, **extra):
    answer = dict(run=str(root), accepted=accepted, state=state['state'], generation=state['generation'],
                automation_id=state.get('automation_id'), delivery=state.get('delivery'),
                effects=effects(root, state, dispatch=dispatch) if accepted and include_effects else {}, **extra)
    if answer['effects']:
        answer['execute_effects'] = dispatch_code(root, state)
    return answer


def act(root, action, **args):
    root = Path(root)
    with control_lock(root):
        state = load(root)
        if action == 'status':
            return dict(state, run=str(root))
        if action == 'effects':
            return response(root, state, dispatch=True)
        if action == 'context':
            return dict(objective=state['objective'], workspace=state.get('workspace'),
                        context_summary=state.get('context_summary'), wait=state.get('wait'),
                        last_check=state.get('last_check'), last_decision=state.get('last_decision'))
        if action == 'context-update':
            if os.environ.get('CODEX_THREAD_ID') != state['thread_id']:
                return response(root, state, accepted=False, reason='Only the business chat may update its context')
            if args.get('context_summary'):
                state['context_summary'] = args['context_summary']
            if args.get('decision'):
                state['last_decision'] = args['decision']
            save(root, state, 'context_updated')
            return response(root, state, include_effects=False)
        if action == 'turn-ended':
            # Only an owning private App Server client may supply this actual event.
            if args.get('thread_id') != state['thread_id'] or not args.get('turn_id'):
                return response(root, state, accepted=False)
            if args['turn_id'] in state.get('seen_turn_ids', []) or args['turn_id'] in state.get('continued_turn_ids', []):
                return response(root, state, accepted=False)
            state.setdefault('seen_turn_ids', []).append(args['turn_id'])
            if args.get('turn_status') != 'completed' or state['state'] != 'RUNNING':
                save(root, state, 'turn_event_ignored', turn_id=args['turn_id'])
                return response(root, state, accepted=False)
            if state.get('delivery') and state['delivery']['state'] != 'ACKNOWLEDGED':
                save(root, state, 'turn_event_ignored', turn_id=args['turn_id'])
                return response(root, state, accepted=False)
            state.setdefault('continued_turn_ids', []).append(args['turn_id'])
            state['delivery'] = dict(ticket=uuid.uuid4().hex[:16], state='PENDING', cause='objective_unfinished')
            save(root, state, 'turn_continuation_admitted', turn_id=args['turn_id'])
            return response(root, state, dispatch=True)
        if action == 'wait':
            if state['state'] != 'RUNNING':
                raise ValueError('Only a running goal can register a new wait')
            note = {k: args.get(k) for k in ['waiting_for', 'completion', 'result_location', 'guide',
                                         'interval_minutes', 'timeout_minutes', 'max_checks']}
            note['guide'] = note['guide'] or '根据目标与完成标准自主只读调查；检查程序可选，工具错误时寻找其他已授权证据。'
            if not all(isinstance(note[k], str) and note[k].strip() for k in ['waiting_for', 'completion', 'result_location']):
                raise ValueError('Waiting for, completion criteria and result location are required')
            if any(note[k] < 1 for k in ['interval_minutes', 'timeout_minutes', 'max_checks']):
                raise ValueError('Check limits must be positive')
            enter_wait(state, note)
            save(root, state, 'wait_registered', generation=state['generation'])
        elif action == 'bind':
            if not args['automation_id'] or state['state'] != 'WAITING' or state.get('automation_id') not in [None, args['automation_id']]:
                raise ValueError('Cannot replace a different automation or bind an inactive wait')
            state['automation_id'] = args['automation_id']
            save(root, state, 'automation_bound', automation_id=args['automation_id'])
        elif action in ['pause', 'blocked']:
            if state['state'] not in ['RUNNING', 'WAITING']:
                return response(root, state, accepted=False)
            state['before_pause'] = state['state']
            state['state'] = 'PAUSED_BY_USER' if action == 'pause' else 'BLOCKED'
            state['reason'] = args.get('reason') or '用户暂停'
            state['generation'] += 1
            save(root, state, action, reason=state['reason'])
        elif action == 'resume':
            if state['state'] not in ['PAUSED_BY_USER', 'BLOCKED', 'NEEDS_REVIEW']:
                raise ValueError('Only an explicitly paused or blocked goal can be resumed')
            if state.get('before_pause') == 'WAITING':
                enter_wait(state, state['wait'])
            else:
                state.update(state='RUNNING', generation=state['generation'] + 1, delivery=None)
                state.pop('reason', None)
            state.pop('before_pause', None)
            save(root, state, 'manual_resume', generation=state['generation'])
        elif action in ['check', 'report']:
            if state.get('trigger_kind') == 'heartbeat' and os.environ.get('CODEX_THREAD_ID') != state['checker_thread_id']:
                return response(root, state, accepted=False, eligible=False, reason='Not the registered checker chat')
            if state['state'] != 'WAITING' or args['generation'] != state['generation']:
                answer = response(root, state, accepted=False, eligible=False)
                if action == 'check' and state['state'] != 'WAITING' and state.get('automation_id'):
                    answer['effects'] = effects(root, state)  # Stop a timer whose earlier pause was delayed.
                    answer['execute_effects'] = dispatch_code(root, state)
                return answer
            note = state['wait']
            if action == 'check':
                if not state.get('automation_id'):
                    return response(root, state, accepted=False, eligible=False)
                if time.time() > note['created_at'] + note['timeout_minutes'] * 60 or note['checks'] >= note['max_checks']:
                    state.update(state='NEEDS_REVIEW', before_pause='WAITING', reason='观察预算已用完；暂停观察，业务状态未被证明阻塞或失败')
                    state['generation'] += 1
                    save(root, state, 'check_limit', reason=state['reason'])
                    return response(root, state, eligible=False)
                note['checks'] += 1
                observation_id = f"{state['generation']}-{note['checks']}"
                state['active_observation'] = observation_id
                save(root, state, 'checker_admitted', generation=state['generation'],
                     checker_thread_id=os.environ.get('CODEX_THREAD_ID'), check_number=note['checks'],
                     observation_id=observation_id)
                context = dict(objective=state['objective'], workspace=state.get('workspace'),
                               summary=state.get('context_summary', ''), wait=note,
                               last_check=state.get('last_check'), last_decision=state.get('last_decision'))
                return response(root, state, eligible=True, wait=note, context=context,
                                observation_id=observation_id, instructions=observation_instructions(root, state),
                                include_effects=False)
            if not args.get('observation_id') or args['observation_id'] != state.get('active_observation'):
                return response(root, state, accepted=False, reason='No matching admitted observation')
            task_state = args.get('task_state')
            outcome = args.get('result') or {'running':'pending', 'completed':'ready',
                                           'failed':'failed', 'uncertain':'unknown'}.get(task_state)
            if outcome not in ['pending', 'ready', 'blocked', 'unknown', 'failed'] or not args['reason'].strip():
                raise ValueError('A supported result and nonempty reason are required')
            mapped = dict(pending='running', ready='completed', failed='failed', unknown='uncertain', blocked='uncertain')[outcome]
            if task_state and task_state != mapped:
                raise ValueError('result and task-state disagree')
            observation_state = args.get('observation_state') or ('insufficient_evidence' if outcome == 'unknown' else 'ok')
            if observation_state not in ['ok', 'tool_error', 'insufficient_evidence']:
                raise ValueError('Unknown observation state')
            if outcome in ['ready', 'failed'] and not args.get('evidence', '').strip():
                raise ValueError('A terminal task result needs actual evidence')
            if outcome == 'blocked' and args.get('blocker_kind') not in ['authorization','input','safety','user']:
                raise ValueError('blocked requires a genuine user/input/authorization/safety reason')
            state['last_check'] = dict(result=outcome, reason=args['reason'], evidence=args['evidence'],
                                       task_state=mapped, observation_state=observation_state,
                                       observation_id=args['observation_id'], blocker_kind=args.get('blocker_kind'),
                                       checked_at=time.time(), checker_thread_id=os.environ.get('CODEX_THREAD_ID'))
            state.pop('active_observation', None)
            if outcome in ['ready', 'failed']:
                assert wake_once(state, args['generation'])
                state['delivery'] = dict(ticket=uuid.uuid4().hex[:16], state='PENDING', cause=outcome)
            elif outcome == 'blocked':
                state.update(state='BLOCKED', before_pause='WAITING', reason=args['reason'])
                state['generation'] += 1
            save(root, state, 'check_reported', generation=args['generation'], result=state['last_check'])
            return response(root, state, dispatch=outcome in ['ready', 'failed'], include_effects=outcome in ['ready', 'blocked', 'failed'])
        elif action in ['claim', 'sent', 'delivery-error', 'acknowledge']:
            if state.get('observer_mode') == 'same-chat' and action != 'acknowledge':
                return response(root, state, accepted=False, reason='Same-chat continuation is local; no message transport')
            delivery = state.get('delivery')
            if not delivery or delivery['ticket'] != args['ticket']:
                return response(root, state, accepted=False)
            if action == 'claim':
                if state['state'] != 'RUNNING' or delivery['state'] != 'PENDING':
                    return response(root, state, accepted=False)
                delivery['state'] = 'DISPATCHING'
            elif action == 'acknowledge':
                actual = args.get('thread_id') or os.environ.get('CODEX_THREAD_ID')
                if state['state'] != 'RUNNING' or delivery['state'] not in ['PENDING', 'DISPATCHING', 'SENT'] or actual != state['thread_id']:
                    return response(root, state, accepted=False)
                delivery.update(state='ACKNOWLEDGED', acknowledged_by=actual, acknowledged_at=time.time())
            elif action == 'sent':
                if delivery['state'] != 'ACKNOWLEDGED':
                    delivery['state'] = 'SENT'
                delivery['sent_at'] = time.time()
            else:
                if delivery['state'] == 'ACKNOWLEDGED' or state['state'] == 'DONE':
                    return response(root, state, accepted=False)
                state.update(state='NEEDS_REVIEW', before_pause='RUNNING', reason=args['reason'])
                delivery.update(state='ERROR', error=args['reason'])
            save(root, state, action, ticket=args['ticket'])
        elif action == 'complete':
            if state['state'] != 'RUNNING':
                raise ValueError('Only a running goal can complete')
            if state.get('delivery') and state['delivery']['state'] != 'ACKNOWLEDGED':
                raise ValueError('A wake must be acknowledged in its original conversation first')
            state.update(state='DONE', summary=args['reason'], completed_at=time.time())
            save(root, state, 'goal_completed', summary=args['reason'])
        else:
            raise ValueError('Unknown action')
        return response(root, state, include_effects=action not in ['bind', 'claim', 'sent', 'acknowledge'])


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['create', 'wait', 'status', 'bind', 'pause', 'resume', 'blocked',
                                     'complete', 'check', 'report', 'claim', 'sent', 'delivery-error',
                                     'acknowledge', 'effects', 'context', 'context-update', 'turn-ended'])
    targets = p.add_mutually_exclusive_group()
    targets.add_argument('--run', type=Path)
    targets.add_argument('--run-id')
    p.add_argument('--objective')
    p.add_argument('--thread-id')
    p.add_argument('--project-id')
    p.add_argument('--checker-thread-id')
    p.add_argument('--observer-mode', choices=['same-chat'], default='same-chat')
    p.add_argument('--workspace', type=Path)
    p.add_argument('--context-summary', default='')
    p.add_argument('--decision')
    p.add_argument('--model')
    p.add_argument('--continuation', default='继续原目标，先读取检查结果再行动。')
    p.add_argument('--waiting-for')
    p.add_argument('--completion')
    p.add_argument('--result-location')
    p.add_argument('--guide')
    p.add_argument('--interval-minutes', type=int, default=1)
    p.add_argument('--timeout-minutes', type=int, default=15)
    p.add_argument('--max-checks', type=int, default=4)
    p.add_argument('--generation', type=int)
    p.add_argument('--observation-id')
    p.add_argument('--task-state', choices=['running','completed','failed','uncertain'])
    p.add_argument('--observation-state', choices=['ok','tool_error','insufficient_evidence'])
    p.add_argument('--blocker-kind', choices=['authorization','input','safety','user'])
    p.add_argument('--result', choices=['pending', 'ready', 'blocked', 'unknown', 'failed'])
    p.add_argument('--reason', default='')
    p.add_argument('--evidence', default='')
    p.add_argument('--automation-id')
    p.add_argument('--ticket')
    p.add_argument('--turn-id')
    p.add_argument('--turn-status', choices=['completed','interrupted','failed'])
    a = vars(p.parse_args())
    action, root, run_id = a.pop('action'), a.pop('run'), a.pop('run_id')
    try:
        if run_id is not None:
            if action == 'create':
                raise ValueError('run-id selects an existing goal; it does not create one')
            root = resolve_run_id(run_id)
        if action == 'create':
            if not all(a[k] for k in ['objective', 'thread_id', 'project_id']):
                raise ValueError('objective, thread-id and project-id are required')
            root = create(**{k: a[k] for k in ['objective', 'thread_id', 'project_id', 'checker_thread_id',
                                             'model', 'continuation', 'observer_mode', 'workspace', 'context_summary']})
            answer = dict(run=str(root), state='RUNNING')
        else:
            if root is None or not (root / 'state.json').is_file():
                raise ValueError('Existing goal state is required (--run-id or --run)')
            if action in ['acknowledge', 'turn-ended']:
                a['thread_id'] = os.environ.get('CODEX_THREAD_ID')
            answer = act(root.resolve(), action, **a)
        print(json.dumps(answer, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
