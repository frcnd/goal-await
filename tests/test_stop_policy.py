"""Installed Stop policy on synthetic .167 transcripts; no live chat or timer."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CHAT = '11111111-1111-1111-1111-111111111111'
TURN = 'fixture-turn'


class StopPolicy(unittest.TestCase):
    def setUp(self):
        base = REPO / '.test-work'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'project'
        self.project.mkdir()
        (self.project/'.git').mkdir()
        self.home = self.root/'home'
        self.home.mkdir()
        self.codex = self.home/'.codex'
        self.env = dict(os.environ,CODEX_THREAD_ID=CHAT,CODEX_HOME=str(self.codex),PYTHONDONTWRITEBYTECODE='1')
        self.run_cli(REPO/'tools/install_user.py','install','--home',self.home,'--codex-home',self.codex)
        self.scripts = self.home/'.agents/skills/goal-await/scripts'
        self.transcript = self.codex/'sessions/2026/11/07'/f'rollout-stop-{CHAT}.jsonl'
        self.transcript.parent.mkdir(parents=True)
        self.serial = 0
        self.append('session_meta',dict(id=CHAT,cwd=str(self.project),cli_version='0.167.0-alpha.3.2'))
        self.append('event_msg',dict(type='task_started',turn_id=TURN,turn_attribution=dict(turn_trigger='composer')))
        self.append('turn_context',dict(turn_id=TURN,cwd=str(self.project),model='fixture-model'))
        self.objective = 'Preserve the complete objective: deliver all requested files and verify every requirement.'
        started = self.run_cli(self.scripts/'goal_entry.py','start','--objective',self.objective)
        self.state_path = Path(started['run'])/'state.json'
        self.event = dict(hook_event_name='Stop',session_id=CHAT,turn_id=TURN,
                          cwd=str(self.project),transcript_path=str(self.transcript),stop_hook_active=False)

    def run_cli(self,script,*args,input=None):
        result = subprocess.run([sys.executable,'-B','-X','utf8',str(script),*map(str,args)],
            cwd=self.project,env=self.env,input=input,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def append(self,kind,payload):
        self.serial += 1
        with self.transcript.open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(dict(timestamp=f'fixture-{self.serial}',type=kind,payload=payload))+'\n')

    def item(self,kind,**fields):
        self.append('event_msg',dict(type='item_completed',thread_id=CHAT,turn_id=TURN,
            item=dict(type=kind,id=f'item-{self.serial}',**fields)))

    def command(self,command='python verify_artifact.py'):
        self.item('CommandExecution',status='completed',exit_code=0,command=['pwsh','-Command',command])

    def answer(self):
        self.item('AgentMessage',phase='final_answer',content=[dict(type='output_text',text='I will continue later.')])

    def stop(self):
        result = self.run_cli(self.scripts/'goal_await_stop.py','--bindings',self.codex/'goal-await/bindings',
                              input=json.dumps(self.event))
        self.event['stop_hook_active'] = True
        return result

    def state(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def test_full_objective_retained_and_text_only_correction_happens_once(self):
        self.answer()
        first = self.stop()
        self.assertEqual(first['decision'],'block')
        self.assertIn(self.objective,first['reason'])
        self.answer()
        stopped = self.stop()
        self.assertNotIn('decision',stopped)
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')
        self.assertEqual(self.state()['stop_hook']['continuations'],1)

    def test_business_activity_allows_next_step_but_not_restatement(self):
        self.command()
        self.assertEqual(self.stop()['decision'],'block')
        self.answer()
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['stop_hook']['last_kind'],'no_activity')

    def test_control_commands_do_not_keep_a_continuation_alive(self):
        self.command('python C:/fixture/goal_entry.py context')
        self.assertEqual(self.stop()['decision'],'block')
        self.command('python C:/fixture/goal_entry.py status')
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')

    def test_duplicate_callback_does_not_spend_an_admission(self):
        self.command()
        self.assertEqual(self.stop()['decision'],'block')
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['stop_hook']['continuations'],1)

    def test_real_activity_still_respects_three_continuation_cap(self):
        for _ in range(3):
            self.command()
            self.assertEqual(self.stop()['decision'],'block')
        self.command()
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')
        self.assertEqual(self.state()['stop_hook']['continuations'],3)

    def test_same_turn_wait_does_not_reset_counter_or_replay_earlier_tools(self):
        for _ in range(3):
            self.command()
            self.assertEqual(self.stop()['decision'],'block')
        waiting = self.run_cli(self.scripts/'goal_entry.py','wait','--waiting-for','fixture result',
            '--completion','fixture ready','--result-location','fixture.json',
            '--progress-evidence','offline fixture job already started')
        self.run_cli(self.scripts/'goal_entry.py','bind','--automation-id','fixture-timer')
        generation = str(waiting['generation'])
        run_id = self.state()['run_id']
        checked = self.run_cli(self.scripts/'goal_await.py','check','--run-id',run_id,'--generation',generation)
        ready = self.run_cli(self.scripts/'goal_await.py','report','--run-id',run_id,'--generation',generation,
            '--observation-id',checked['observation_id'],'--task-state','completed',
            '--reason','fixture ready','--evidence','fixture output')
        self.run_cli(self.scripts/'goal_await.py','acknowledge','--run-id',run_id,
                     '--ticket',ready['effects']['ticket'])
        self.command('python C:/fixture/goal_entry.py context')
        self.answer()
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')
        self.assertEqual(self.state()['stop_hook']['continuations'],3)
        self.assertEqual(self.state()['stop_hook']['last_kind'],'no_activity')

    def test_pending_native_tool_is_quiet_and_its_result_can_make_progress(self):
        self.append('response_item',dict(type='custom_tool_call',call_id='outer-1',name='exec',status='completed',input='...'))
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['state'],'RUNNING')
        self.assertEqual(self.state()['stop_hook']['continuations'],0)
        self.command()
        self.append('response_item',dict(type='custom_tool_call_output',call_id='outer-1',output='...'))
        self.assertEqual(self.stop()['decision'],'block')

    def test_plan_and_interruption_do_not_receive_business_nudges(self):
        self.append('turn_context',dict(turn_id=TURN,cwd=str(self.project),model='fixture-model',
                                        collaboration_mode=dict(mode='plan')))
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['stop_hook']['continuations'],0)
        self.append('event_msg',dict(type='turn_aborted',turn_id=TURN,reason='interrupted'))
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['state'],'PAUSED_BY_USER')

    def test_automatic_round_has_no_text_only_bootstrap(self):
        auto = 'fixture-automatic-turn'
        self.append('event_msg',dict(type='task_started',turn_id=auto,turn_trigger='automation_heartbeat_scheduled'))
        self.append('turn_context',dict(turn_id=auto,cwd=str(self.project),model='fixture-model'))
        self.event['turn_id'] = auto
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')
        self.assertEqual(self.state()['stop_hook']['continuations'],0)

    def test_native_continuation_flag_does_not_grant_a_fresh_correction(self):
        self.answer()
        self.event['stop_hook_active'] = True
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')
        self.assertEqual(self.state()['stop_hook']['continuations'],0)

    def test_missing_or_partial_evidence_is_not_a_reason_to_nudge(self):
        with self.transcript.open('a',encoding='utf-8') as handle:
            handle.write('{"partial":')
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')

    def test_corrupt_evidence_after_a_prior_stop_is_not_a_duplicate_callback(self):
        self.command()
        self.assertEqual(self.stop()['decision'],'block')
        with self.transcript.open('a',encoding='utf-8') as handle:
            handle.write('{"partial":')
        result = self.stop()
        self.assertNotIn('decision',result)
        self.assertEqual(self.state()['state'],'NEEDS_REVIEW')

    def test_hook_feedback_and_a_status_reply_are_not_new_progress(self):
        self.command()
        self.assertEqual(self.stop()['decision'],'block')
        self.item('HookPrompt',fragments=[dict(hookRunId='stop:0:fixture/hooks.json',text=self.objective)])
        self.answer()
        self.assertNotIn('decision',self.stop())
        self.assertEqual(self.state()['stop_hook']['last_kind'],'no_activity')
        self.assertEqual(self.state()['stop_hook']['continuations'],1)

    def test_pending_child_and_empty_wait_are_quiet_until_native_completion(self):
        child = '22222222-2222-2222-2222-222222222222'
        self.item('CollabAgentToolCall',status='completed',tool='spawn_agent',
                  receiver_thread_ids=[child],agents_states={child:'pending_init'})
        self.assertEqual(self.stop(),{})
        self.item('CollabAgentToolCall',status='completed',tool='wait',
                  receiver_thread_ids=[],agents_states={})
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['stop_hook']['continuations'],0)
        text = '<subagent_notification>'+json.dumps(dict(agent_path=child,status=dict(completed='fixture done')))+'</subagent_notification>'
        self.append('response_item',dict(type='message',role='user',content=[dict(type='input_text',text=text)],
            internal_chat_message_metadata_passthrough=dict(turn_id=TURN,
                content_item_kinds=['multi_agent.subagent_notification'],
                content_item_metadata=[dict(provenance=dict(type='tool',namespace='multi_agent_v1'))])))
        self.assertEqual(self.stop()['decision'],'block')

    def test_recovery_snapshot_does_not_block_stop_after_model_switch(self):
        self.run_cli(self.scripts/'goal_entry.py','recovery-enable')
        self.append('turn_context',dict(turn_id=TURN,cwd=str(self.project),model='new-model'))
        self.command()
        self.assertEqual(self.stop()['decision'],'block')
        self.assertEqual(self.state()['state'],'RUNNING')
        self.assertEqual(self.state()['recovery']['attempts'],0)
        self.append('turn_context',dict(turn_id=TURN,cwd=str(self.project)))
        self.command('python verify_second_artifact.py')
        self.assertEqual(self.stop()['decision'],'block')
        self.assertEqual(self.state()['stop_hook']['continuations'],2)
        self.assertEqual(self.state()['recovery']['attempts'],0)

    def test_existing_pause_remains_authoritative(self):
        self.run_cli(self.scripts/'goal_entry.py','pause','--reason','fixture user pause')
        self.command()
        self.assertEqual(self.stop(),{})
        self.assertEqual(self.state()['state'],'PAUSED_BY_USER')


if __name__ == '__main__':
    unittest.main()
