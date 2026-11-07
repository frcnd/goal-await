"""Installed runtime with synthetic host transcripts; no model or real timer."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
CHAT = '11111111-1111-1111-1111-111111111111'
MODEL = 'fixture/provider-model'
ERROR = 'Incomplete response returned, reason: max_output_tokens'


class FailureRecovery(unittest.TestCase):
    def setUp(self):
        work = REPO / '.test-work'
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.codex = self.home / '.codex'
        self.env = dict(os.environ, CODEX_THREAD_ID=CHAT, CODEX_HOME=str(self.codex),
                        PYTHONDONTWRITEBYTECODE='1')
        self.call(REPO / 'tools/install_user.py', 'install', '--home', self.home,
                  '--codex-home', self.codex)
        self.entry = self.home / '.agents/skills/goal-await/scripts/goal_entry.py'
        self.core = self.entry.with_name('goal_await.py')
        self.transcript = self.codex / 'sessions/2026/11/07' / ('rollout-fixture-' + CHAT + '.jsonl')
        self.transcript.parent.mkdir(parents=True)
        self.append('session_meta', id=CHAT, cwd=str(self.project), cli_version='0.167.0-alpha.3.2')
        self.start_turn('original', 'composer')
        self.goal = self.call(self.entry, 'start', '--objective', 'deliver fixture result',
                              '--recover-errors', '--recovery-max-attempts', '2')
        self.run_id = self.goal['run_id']
        self.state_path = Path(self.goal['run']) / 'state.json'
        self.call(self.entry, 'bind', '--automation-id', 'fixture-timer')

    def call(self, script, *args, chat=CHAT, ok=True, cwd=None):
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script), *map(str, args)],
            cwd=cwd or self.project, env=dict(self.env, CODEX_THREAD_ID=chat), capture_output=True,
            text=True, encoding='utf-8', timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def append(self, kind, **payload):
        with self.transcript.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(type=kind, payload=payload)) + '\n')

    def start_turn(self, turn_id, trigger, model=MODEL, automation_id='fixture-timer'):
        self.append('event_msg', type='task_started', turn_id=turn_id,
                    turn_attribution=dict(turn_id=turn_id, turn_trigger=trigger))
        self.append('turn_context', turn_id=turn_id, cwd=str(self.project), model=model)
        if trigger.startswith('automation_heartbeat_'):
            self.append('response_item', type='message', role='user',
                        content=[dict(type='input_text', text='<heartbeat>\n<automation_id>' +
                            automation_id + '</automation_id>\n</heartbeat>')],
                        internal_chat_message_metadata_passthrough=dict(turn_id=turn_id,
                            content_item_metadata=[dict(provenance=dict(type='user'))]))

    def finish(self, turn_id, error=ERROR):
        self.append('event_msg', type='task_complete', turn_id=turn_id,
                    completed_at=time.time() + 10, error=dict(message=error) if error else None)

    def check(self, generation='0'):
        return self.call(self.entry, 'recover-check', '--generation', generation)

    def ack(self, receipt):
        return self.call(self.core, 'acknowledge', '--run-id', self.run_id,
                         '--ticket', receipt['effects']['ticket'])

    def test_one_shot_budget_and_preserved_model(self):
        spec = self.goal['effects']['automation']
        self.assertEqual(spec['targetThreadId'], CHAT)
        self.assertEqual(spec['kind'], 'heartbeat')
        self.assertTrue(spec['rrule'].startswith('RDATE:'))
        self.assertNotIn('model', spec)
        self.assertNotIn('reasoningEffort', spec)
        self.finish('original')
        self.start_turn('recovery-1', 'automation_heartbeat_scheduled')
        first = self.check()
        self.assertTrue(first['eligible'])
        self.assertEqual(first['effects']['automation']['id'], 'fixture-timer')
        self.assertEqual(first['effects']['automation']['status'], 'ACTIVE')
        self.assertIn('1/2', first['effects']['local_continuation']['prompt'])
        self.assertEqual(json.loads(self.state_path.read_text())['recovery']['attempts'], 1)
        self.ack(first)
        self.finish('recovery-1')
        self.start_turn('recovery-2', 'automation_heartbeat_scheduled')
        second = self.check()
        self.assertTrue(second['eligible'])
        self.ack(second)
        self.finish('recovery-2')
        self.start_turn('settlement', 'automation_heartbeat_scheduled')
        exhausted = self.check()
        self.assertFalse(exhausted['eligible'])
        self.assertEqual(exhausted['state'], 'NEEDS_REVIEW')
        self.assertEqual(exhausted['effects']['automation']['status'], 'PAUSED')
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state['recovery']['attempts'], 2)
        self.assertEqual(state['recovery']['snapshot']['model'], MODEL)

    def test_duplicate_gate_does_not_consume_a_second_attempt(self):
        self.finish('original')
        self.start_turn('recovery-1', 'automation_heartbeat_scheduled')
        self.assertTrue(self.check()['eligible'])
        duplicate = self.check()
        self.assertFalse(duplicate['accepted'])
        self.assertEqual(duplicate['effects'], {})
        self.assertEqual(json.loads(self.state_path.read_text())['recovery']['attempts'], 1)

    def test_model_drift_and_unknown_error_do_not_admit(self):
        self.finish('original', error='unauthorized')
        self.start_turn('tick', 'automation_heartbeat_scheduled', model='different-model')
        receipt = self.check()
        self.assertFalse(receipt['eligible'])
        self.assertEqual(receipt['effects']['automation']['status'], 'PAUSED')
        self.assertEqual(json.loads(self.state_path.read_text())['recovery']['attempts'], 0)

    def test_wait_uses_same_timer_and_ack_rearms_one_shot(self):
        waiting = self.call(self.entry, 'wait', '--waiting-for', 'fixture', '--completion', 'ready',
                            '--result-location', 'fixture.json')
        spec = waiting['effects']['automation']
        self.assertEqual(spec['id'], 'fixture-timer')
        self.assertTrue(spec['rrule'].startswith('RDATE:'))
        self.assertEqual(self.check()['effects'], {})  # Old recovery prompt cannot replace current wait.
        admitted = self.call(self.core, 'check', '--run-id', self.run_id, '--generation', '1')
        self.assertTrue(admitted['effects']['automation']['rrule'].startswith('RDATE:'))
        self.assertEqual(admitted['effects']['automation']['id'], 'fixture-timer')
        result = self.call(self.core, 'report', '--run-id', self.run_id, '--generation', '1',
            '--observation-id', admitted['observation_id'], '--task-state', 'completed',
            '--reason', 'fixture ready', '--evidence', 'offline file')
        self.assertEqual(result['effects']['automation']['status'], 'PAUSED')
        rearmed = self.ack(result)
        self.assertEqual(rearmed['effects']['automation']['status'], 'ACTIVE')
        self.assertTrue(rearmed['effects']['automation']['rrule'].startswith('RDATE:'))
        completed = self.call(self.entry, 'complete', '--reason', 'fixture delivered')
        self.assertEqual(completed['effects']['automation']['status'], 'PAUSED')

    def test_pause_stale_and_foreign_chat_cannot_recover(self):
        foreign = self.call(self.core, 'recover-check', '--run-id', self.run_id,
                            '--generation', '0', chat='22222222-2222-2222-2222-222222222222')
        self.assertFalse(foreign['accepted'])
        paused = self.call(self.entry, 'pause', '--reason', 'user asked')
        self.assertEqual(paused['effects']['automation']['status'], 'PAUSED')
        stopped = self.check()
        self.assertFalse(stopped['eligible'])
        self.assertEqual(stopped['effects']['automation']['status'], 'PAUSED')
        self.assertEqual(json.loads(self.state_path.read_text())['recovery']['attempts'], 0)
        resumed = self.call(self.entry, 'resume')
        self.assertEqual(resumed['effects']['automation']['id'], 'fixture-timer')
        self.assertFalse(self.check()['eligible'])

    def test_unconfirmed_delivery_is_not_replayed(self):
        self.finish('original')
        self.start_turn('recovery-1', 'automation_heartbeat_scheduled')
        self.check()  # Simulate failure before acknowledge.
        self.finish('recovery-1')
        self.start_turn('recovery-2', 'automation_heartbeat_scheduled')
        receipt = self.check()
        self.assertFalse(receipt['eligible'])
        self.assertEqual(receipt['state'], 'NEEDS_REVIEW')
        self.assertEqual(json.loads(self.state_path.read_text())['recovery']['attempts'], 1)

    def test_stale_heartbeat_cannot_fall_through_normal_stop(self):
        self.call(self.entry, 'pause', '--reason', 'fixture pause')
        resumed = self.call(self.entry, 'resume')
        generation = resumed['generation']
        self.finish('original')
        self.start_turn('stale-tick', 'automation_heartbeat_scheduled')
        denied = self.check('0')
        self.assertFalse(denied['eligible'])
        self.assertEqual(denied['effects'], {})
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state['state'], 'RUNNING')
        self.assertEqual(state['generation'], generation)
        stop_script = self.entry.with_name('goal_await_stop.py')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(stop_script),
            '--bindings', str(self.codex / 'goal-await/bindings')],
            input=json.dumps(dict(hook_event_name='Stop', session_id=CHAT,
                turn_id='stale-tick', cwd=str(self.project))),
            cwd=self.project, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_wrong_shell_workspace_denies_original_workspace_stop(self):
        self.finish('original')
        self.start_turn('wrong-cwd-tick', 'automation_heartbeat_scheduled')
        denied = self.call(self.entry, 'recover-check', '--generation', '0', cwd=self.base)
        self.assertFalse(denied['eligible'])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state['recovery_denied_turn_id'], 'wrong-cwd-tick')
        self.assertEqual(state['recovery']['attempts'], 0)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.entry.with_name('goal_await_stop.py')),
            '--bindings', str(self.codex / 'goal-await/bindings')],
            input=json.dumps(dict(hook_event_name='Stop', session_id=CHAT,
                turn_id='wrong-cwd-tick', cwd=str(self.project))),
            cwd=self.project, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})

    def test_waiting_and_ack_refuse_a_fallback_model(self):
        self.call(self.entry, 'wait', '--waiting-for', 'fixture', '--completion', 'ready',
                  '--result-location', 'fixture.json')
        admitted = self.call(self.core, 'check', '--run-id', self.run_id, '--generation', '1')
        result = self.call(self.core, 'report', '--run-id', self.run_id, '--generation', '1',
            '--observation-id', admitted['observation_id'], '--task-state', 'completed',
            '--reason', 'fixture ready', '--evidence', 'offline file')
        self.finish('original', error=None)
        self.start_turn('fallback', 'automation_heartbeat_scheduled', model='other-model')
        rejected = self.ack(result)
        self.assertFalse(rejected['accepted'])
        self.assertEqual(rejected['effects']['automation']['status'], 'PAUSED')
        self.assertEqual(rejected['state'], 'NEEDS_REVIEW')
        self.assertEqual(json.loads(self.state_path.read_text())['delivery']['state'], 'PENDING')

    def test_waiting_check_refuses_a_fallback_before_observation(self):
        self.call(self.entry, 'wait', '--waiting-for', 'fixture', '--completion', 'ready',
                  '--result-location', 'fixture.json')
        self.finish('original', error=None)
        self.start_turn('fallback', 'automation_heartbeat_scheduled', model='other-model')
        rejected = self.call(self.core, 'check', '--run-id', self.run_id, '--generation', '1')
        self.assertFalse(rejected['eligible'])
        self.assertEqual(rejected['effects']['automation']['status'], 'PAUSED')
        self.assertEqual(json.loads(self.state_path.read_text())['wait']['checks'], 0)

    def test_waiting_before_gate_failure_has_no_recurring_schedule(self):
        waiting = self.call(self.entry, 'wait', '--waiting-for', 'fixture', '--completion', 'ready',
                            '--result-location', 'fixture.json')
        schedule = waiting['effects']['automation']['rrule']
        # A scheduler consumes the only date on dispatch. Failure before check
        # executes has no recurrence and no emitted update to rearm it.
        self.assertRegex(schedule, r'^RDATE:\d{8}T\d{6}Z$')
        self.assertNotIn('FREQ=', schedule)
        self.assertEqual(json.loads(self.state_path.read_text())['wait']['checks'], 0)

    def test_waiting_interrupt_does_not_restart_business(self):
        self.call(self.entry, 'wait', '--waiting-for', 'fixture', '--completion', 'ready',
                  '--result-location', 'fixture.json')
        self.append('event_msg', type='turn_aborted', turn_id='original', reason='interrupted')
        self.start_turn('after-interrupt', 'automation_heartbeat_scheduled')
        rejected = self.call(self.core, 'check', '--run-id', self.run_id, '--generation', '1')
        self.assertFalse(rejected['eligible'])
        self.assertEqual(rejected['effects']['automation']['status'], 'PAUSED')
        self.assertEqual(json.loads(self.state_path.read_text())['wait']['checks'], 0)

    def test_stale_waiting_check_after_interrupt_cannot_fall_through_stop(self):
        self.call(self.entry, 'pause', '--reason', 'fixture pause')
        self.call(self.entry, 'resume')
        self.append('event_msg', type='turn_aborted', turn_id='original', reason='interrupted')
        self.start_turn('stale-wait-tick', 'automation_heartbeat_scheduled')
        rejected = self.call(self.core, 'check', '--run-id', self.run_id, '--generation', '0')
        self.assertFalse(rejected['eligible'])
        self.assertEqual(rejected['state'], 'NEEDS_REVIEW')
        self.assertEqual(rejected['effects']['automation']['status'], 'PAUSED')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.entry.with_name('goal_await_stop.py')),
            '--bindings', str(self.codex / 'goal-await/bindings')],
            input=json.dumps(dict(hook_event_name='Stop', session_id=CHAT,
                turn_id='stale-wait-tick', cwd=str(self.project))),
            cwd=self.project, env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})



if __name__ == '__main__':
    unittest.main()
