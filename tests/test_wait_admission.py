"""Wait admission and blocker lifecycle regressions using an isolated install."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
CHAT = '11111111-1111-1111-1111-111111111111'
MODEL = 'fixture/provider-model'
PROGRESS_EVIDENCE = 'offline fixture job already started'
MISSING = object()


class WaitAdmission(unittest.TestCase):
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
                        PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        self.call(REPO / 'tools/install_user.py', 'install', '--home', self.home,
                  '--codex-home', self.codex)
        self.entry_script = self.home / '.agents/skills/goal-await/scripts/goal_entry.py'
        self.core_script = self.entry_script.with_name('goal_await.py')

        self.transcript = (self.codex / 'sessions/2026/11/07' /
                           f'rollout-fixture-{CHAT}.jsonl')
        self.transcript.parent.mkdir(parents=True)
        self.append('session_meta', id=CHAT, cwd=str(self.project),
                    cli_version='0.167.0-alpha.3.2')
        self.append('event_msg', type='task_started', turn_id='fixture-turn',
                    turn_trigger='composer')
        self.append('turn_context', turn_id='fixture-turn', cwd=str(self.project), model=MODEL)

        self.goal = self.entry('start', '--objective', 'deliver fixture result',
                               '--recover-errors', '--recovery-max-attempts', '1')
        self.run_id = self.goal['run_id']
        self.state_path = Path(self.goal['run']) / 'state.json'
        self.context_path = self.state_path.with_name('context.json')
        self.events_path = self.state_path.with_name('events.jsonl')
        self.entry('bind', '--automation-id', 'fixture-recovery-timer')

    def call(self, script, *args, ok=True):
        result = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(script), *map(str, args)],
            cwd=self.project, env=self.env, capture_output=True, text=True,
            encoding='utf-8', timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def entry(self, action, *args, ok=True):
        return self.call(self.entry_script, action, *args, ok=ok)

    def core(self, action, *args, ok=True):
        return self.call(self.core_script, action, '--run-id', self.run_id,
                         *args, ok=ok)

    def append(self, kind, **payload):
        with self.transcript.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type=kind, payload=payload)) + '\n')

    def state(self):
        return json.loads(self.state_path.read_text(encoding='utf-8'))

    def wait_args(self, evidence=PROGRESS_EVIDENCE):
        args = ['--waiting-for', 'fixture result', '--completion', 'fixture is ready',
                '--result-location', 'fixture.json']
        if evidence is not MISSING:
            args.extend(['--progress-evidence', evidence])
        return args

    def start_wait(self, evidence=PROGRESS_EVIDENCE):
        return self.entry('wait', *self.wait_args(evidence))

    def block_from_wait(self):
        waiting = self.start_wait()
        generation = waiting['generation']
        checked = self.core('check', '--generation', generation)
        self.assertTrue(checked['eligible'], checked)
        blocked = self.core(
            'report', '--generation', generation,
            '--observation-id', checked['observation_id'],
            '--task-state', 'uncertain', '--result', 'blocked',
            '--blocker-kind', 'dependency',
            '--reason', 'coordinator must assign an owner and publish the shared registration before work can continue',
            '--evidence', 'coordinator fixture reply: request was not delivered; no owner has accepted it')
        self.assertEqual(blocked['state'], 'BLOCKED')
        return generation, blocked

    def test_invalid_progress_evidence_does_not_touch_recovery_timer(self):
        self.assertEqual(self.state()['automation_id'], 'fixture-recovery-timer')
        self.assertIn('recovery', self.state())
        before = {path: path.read_bytes()
                  for path in (self.state_path, self.context_path, self.events_path)}
        effects = self.entry('effects')
        self.assertEqual(effects['effects']['automation']['mode'], 'update')
        self.assertEqual(effects['effects']['automation']['id'], 'fixture-recovery-timer')

        for evidence in (MISSING, '', '   '):
            with self.subTest(evidence=evidence):
                rejected = self.entry('wait', *self.wait_args(evidence), ok=False)
                self.assertEqual(rejected.stdout, '')
                self.assertRegex(rejected.stderr.lower(),
                                 r'progress[-_ ]?evidence|progress evidence')
                self.assertNotIn('effects', rejected.stderr.lower())
                self.assertEqual(
                    {path: path.read_bytes()
                     for path in (self.state_path, self.context_path, self.events_path)},
                    before)
                self.assertEqual(self.entry('effects'), effects)

    def test_valid_wait_persists_evidence_for_the_checker_context(self):
        evidence = 'opaque external marker 42; caller attests the job already started'
        accepted = self.start_wait(evidence)
        self.assertTrue(accepted['accepted'], accepted)
        self.assertEqual(accepted['state'], 'WAITING')
        state = self.state()
        self.assertEqual(state['wait']['progress_evidence'], evidence)
        self.assertEqual(accepted['effects']['automation']['status'], 'ACTIVE')
        self.assertEqual(accepted['effects']['automation']['id'], 'fixture-recovery-timer')
        context = self.entry('context')
        self.assertEqual(context['wait']['progress_evidence'], evidence)

    def test_unissued_shared_registration_dependency_requires_direct_reason(self):
        before = self.state_path.read_bytes()
        rejected = self.entry('blocked', ok=False)
        self.assertIn('reason', rejected.stderr.lower())
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.state()['state'], 'RUNNING')

        blocked = self.entry(
            'blocked', '--reason',
            'shared registration was not issued; coordinator must assign an owner and publish its location to resume')
        self.assertEqual(blocked['state'], 'BLOCKED')
        self.assertEqual(blocked['effects']['automation']['status'], 'PAUSED')
        state = self.state()
        self.assertEqual(state['before_pause'], 'RUNNING')
        self.assertIn('shared registration was not issued', state['reason'])
        self.assertIsNone(state['delivery'])
        self.assertNotIn('ticket', blocked['effects'])
        self.assertNotIn('local_continuation', blocked['effects'])

    def test_discovered_dependency_report_requires_evidence_and_pauses(self):
        waiting = self.start_wait()
        generation = waiting['generation']
        checked = self.core('check', '--generation', generation)
        self.assertTrue(checked['eligible'], checked)

        rejected = self.core(
            'report', '--generation', generation,
            '--observation-id', checked['observation_id'],
            '--task-state', 'uncertain', '--result', 'blocked',
            '--blocker-kind', 'dependency',
            '--reason', 'coordinator must assign an owner and publish the registration to resume',
            '--evidence', '', ok=False)
        self.assertIn('evidence', rejected.stderr.lower())
        pending = self.state()
        self.assertEqual(pending['state'], 'WAITING')
        self.assertEqual(pending['wait']['checks'], 1)
        self.assertEqual(pending['active_observation'], checked['observation_id'])

        blocked = self.core(
            'report', '--generation', generation,
            '--observation-id', checked['observation_id'],
            '--task-state', 'uncertain', '--result', 'blocked',
            '--blocker-kind', 'dependency',
            '--reason', 'coordinator must assign an owner and publish the registration to resume',
            '--evidence', 'coordinator fixture reply: no owner accepted the registration request; coordination is required')
        self.assertEqual(blocked['state'], 'BLOCKED')
        self.assertEqual(blocked['effects']['automation']['status'], 'PAUSED')
        self.assertIsNone(blocked['delivery'])
        self.assertNotIn('ticket', blocked['effects'])
        self.assertNotIn('local_continuation', blocked['effects'])
        state = self.state()
        self.assertEqual(state['last_check']['blocker_kind'], 'dependency')
        self.assertEqual(state['last_check']['evidence'],
                         'coordinator fixture reply: no owner accepted the registration request; coordination is required')

    def test_blocked_resume_returns_running_without_rearming_wait(self):
        generation, blocked = self.block_from_wait()
        resumed = self.entry('resume')
        self.assertEqual(resumed['state'], 'RUNNING')
        self.assertGreater(resumed['generation'], blocked['generation'])
        self.assertEqual(resumed['effects']['automation']['status'], 'ACTIVE')
        state = self.state()
        self.assertEqual(state['state'], 'RUNNING')
        self.assertIsNone(state.get('wait'))
        self.assertNotIn('before_pause', state)
        self.assertNotEqual(state['generation'], generation)

    def test_legacy_wait_is_denied_before_budget_then_resume_is_running(self):
        waiting = self.start_wait()
        generation = waiting['generation']
        legacy = self.state()
        legacy['wait'].pop('progress_evidence', None)
        self.state_path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2),
                                   encoding='utf-8')

        denied = self.core('check', '--generation', generation)
        self.assertFalse(denied['eligible'])
        self.assertEqual(denied['state'], 'NEEDS_REVIEW')
        self.assertEqual(denied['effects']['automation']['status'], 'PAUSED')
        reviewed = self.state()
        self.assertEqual(reviewed['wait']['checks'], 0)
        self.assertNotIn('active_observation', reviewed)

        resumed = self.entry('resume')
        self.assertEqual(resumed['state'], 'RUNNING')
        self.assertEqual(resumed['effects']['automation']['status'], 'ACTIVE')
        running = self.state()
        self.assertEqual(running['state'], 'RUNNING')
        self.assertIsNone(running.get('wait'))

    def test_user_pause_resume_restores_valid_wait_and_its_proof(self):
        waiting = self.start_wait()
        paused = self.entry('pause', '--reason', 'fixture user pause')
        self.assertEqual(paused['state'], 'PAUSED_BY_USER')
        self.assertEqual(paused['effects']['automation']['status'], 'PAUSED')

        resumed = self.entry('resume')
        self.assertEqual(resumed['state'], 'WAITING')
        self.assertEqual(resumed['effects']['automation']['status'], 'ACTIVE')
        self.assertGreater(resumed['generation'], waiting['generation'])
        state = self.state()
        self.assertEqual(state['wait']['progress_evidence'], PROGRESS_EVIDENCE)
        self.assertEqual(self.entry('context')['wait']['progress_evidence'],
                         PROGRESS_EVIDENCE)


if __name__ == '__main__':
    unittest.main()
