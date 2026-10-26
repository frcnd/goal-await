"""Offline checks for the user-scoped installer; all writes stay in .test-work."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'tools/install_user.py'


def load_installer():
    spec = importlib.util.spec_from_file_location('goal_await_user_install', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UserInstall(unittest.TestCase):
    def setUp(self):
        work = REPO / '.test-work'
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home with spaces'
        self.home.mkdir()
        self.codex = self.home / '.codex'
        self.skill = self.home / '.agents/skills/goal-await'
        self.hook = self.codex / 'hooks.json'
        self.record = self.codex / 'goal-await/install.json'
        self.env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')

    def run_cli(self, action, *args, ok=True):
        command = [sys.executable, '-B', '-X', 'utf8', str(SCRIPT), action,
                   '--home', str(self.home), '--codex-home', str(self.codex),
                   '--python', sys.executable, *map(str, args)]
        result = subprocess.run(command, cwd=self.base, env=self.env, capture_output=True,
                                text=True, encoding='utf-8', timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        payload = next(line for line in reversed(result.stderr.splitlines())
                       if line.lstrip().startswith('{'))
        return json.loads(payload)

    def install(self):
        return self.run_cli('install', '--hook-shell', 'cmd')

    @staticmethod
    def snapshot(root):
        if not root.exists():
            return {}
        return {
            path.relative_to(root).as_posix(): (None if path.is_dir() else path.read_bytes())
            for path in root.rglob('*')
        }

    def test_plan_is_explicit_and_does_not_mutate_home(self):
        before = self.snapshot(self.home)
        plan = self.run_cli('plan', '--hook-shell', 'cmd')
        self.assertEqual(self.snapshot(self.home), before)
        self.assertEqual(plan['scope'], 'user')
        self.assertEqual(plan['skill'], str(self.skill.resolve()))
        self.assertEqual(plan['hook_file'], str(self.hook.resolve()))
        self.assertEqual(plan['data_directory'], str((self.codex / 'goal-await').resolve()))
        self.assertEqual(plan['backup_root'], str((self.codex / 'backups/goal-await-user').resolve()))
        self.assertIn(str(self.skill / 'scripts/goal_await_stop.py'), plan['handler']['command'])
        self.assertIn('--bindings', plan['handler']['command'])
        self.assertEqual(plan['side_effects'], 'none')
        self.assertFalse(plan['writes_config_toml_or_trust'])
        self.assertIn('trust', plan)
        self.assertFalse(self.codex.exists())

    def test_legacy_install_is_rejected_without_mutating_home(self):
        legacy = self.home / '.agents/skills/goal-lite'
        legacy.mkdir(parents=True)
        before = self.snapshot(self.home)
        self.assertIn('goal-lite', self.run_cli('install', ok=False)['error'])
        self.assertEqual(self.snapshot(self.home), before)
        legacy.rmdir()
        record = self.codex / 'goal-lite/install.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'uninstalled': False}), encoding='utf-8')
        before = self.snapshot(self.home)
        self.assertIn('goal-lite', self.run_cli('install', ok=False)['error'])
        self.assertEqual(self.snapshot(self.home), before)
        record.write_text(json.dumps({'uninstalled': True}), encoding='utf-8')
        self.install()
        self.assertEqual(json.loads(record.read_text(encoding='utf-8')), {'uninstalled': True})

    def test_install_coexists_and_uninstall_preserves_hooks_and_state(self):
        self.codex.mkdir()
        original = {
            'hooks': {
                'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'echo existing-pre'}]}],
                'Stop': [{'hooks': [{'type': 'command', 'command': 'echo existing-stop'}]}],
            },
            'unrelated': {'keep': True},
        }
        original_bytes = json.dumps(original, indent=2).encode('utf-8')
        self.hook.write_bytes(original_bytes)

        installed = self.install()
        record = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertEqual(record['scope'], 'user')
        self.assertEqual(record['skill'], str(self.skill.resolve()))
        self.assertEqual(record['handler'], installed['handler'])
        self.assertEqual((Path(record['backup']) / 'hooks.json').read_bytes(), original_bytes)
        self.assertEqual(list(self.skill.parent.glob('.goal-await-user-stage-*')), [])
        runtime = json.loads((self.skill / 'installation.json').read_text(encoding='utf-8'))
        self.assertEqual(set(runtime), {'schema_version', 'scope', 'skill_root',
                                       'data_directory', 'python'})
        self.assertEqual(runtime['scope'], 'user')
        self.assertEqual(runtime['skill_root'], str(self.skill.resolve()))
        self.assertEqual(runtime['data_directory'], str((self.codex / 'goal-await').resolve()))
        skill_text = (self.skill / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('"scope": "user"', skill_text)
        self.assertNotIn('{{PYTHON_JSON}}', skill_text)
        self.assertNotIn('{{ENTRY_JSON}}', skill_text)
        self.assertNotIn('{{SCOPE_JSON}}', skill_text)
        self.assertFalse((self.codex / 'config.toml').exists())

        current = json.loads(self.hook.read_text(encoding='utf-8'))
        self.assertEqual(len(current['hooks']['Stop']), 2)
        current['hooks']['Stop'][1]['hooks'].append(
            {'type': 'command', 'command': 'echo later-same-group'})
        current['hooks']['Stop'].append(
            {'hooks': [{'type': 'command', 'command': 'echo later-group'}]})
        self.hook.write_text(json.dumps(current, indent=2), encoding='utf-8')
        state_path = self.codex / 'goal-await/runs/retained/state.json'
        state_path.parent.mkdir(parents=True)
        state = {'state': 'DONE', 'automation_id': 'native-timer-1'}
        state_path.write_text(json.dumps(state), encoding='utf-8')
        state_bytes = state_path.read_bytes()
        hook_before_refusal = self.hook.read_bytes()

        refused = self.run_cli('uninstall', ok=False)
        self.assertIn('--timers-paused', refused['error'])
        self.assertEqual(self.hook.read_bytes(), hook_before_refusal)
        self.assertTrue(self.skill.exists())

        result = self.run_cli('uninstall', '--timers-paused')
        self.assertTrue(result['uninstalled'])
        final = json.loads(self.hook.read_text(encoding='utf-8'))
        self.assertEqual(final['unrelated'], {'keep': True})
        self.assertEqual(final['hooks']['PreToolUse'], original['hooks']['PreToolUse'])
        commands = [handler['command'] for group in final['hooks']['Stop']
                    for handler in group['hooks']]
        self.assertEqual(commands, ['echo existing-stop', 'echo later-same-group', 'echo later-group'])
        self.assertFalse(self.skill.exists())
        self.assertEqual(state_path.read_bytes(), state_bytes)
        retained_record = json.loads(self.record.read_text(encoding='utf-8'))
        self.assertTrue(retained_record['uninstalled'])
        self.assertFalse((self.codex / 'config.toml').exists())

    def test_uninstall_refuses_edited_installed_files(self):
        self.install()
        hook_before = self.hook.read_bytes()
        refused_reinstall = self.run_cli('install', '--hook-shell', 'cmd', ok=False)
        self.assertIn('Existing Skill', refused_reinstall['error'])
        self.assertEqual(self.hook.read_bytes(), hook_before)
        target = self.skill / 'SKILL.md'
        target.write_text(target.read_text(encoding='utf-8') + '\n# user edit\n', encoding='utf-8')
        before_edit = target.read_bytes()
        refused = self.run_cli('uninstall', ok=False)
        self.assertIn('changed', refused['error'])
        self.assertEqual(self.hook.read_bytes(), hook_before)
        self.assertTrue(self.skill.exists())
        self.assertEqual(target.read_bytes(), before_edit)
        inspected = self.run_cli('inspect')
        self.assertFalse(inspected['ownership_ok'])
        self.assertIn('modified', inspected['ownership_differences'])

    def test_uninstall_removes_newly_created_empty_hook(self):
        self.assertFalse(self.hook.exists())
        self.install()
        self.assertTrue(self.hook.exists())
        result = self.run_cli('uninstall')
        self.assertTrue(result['uninstalled'])
        self.assertFalse(self.hook.exists())
        self.assertFalse(self.skill.exists())
        self.assertTrue(json.loads(self.record.read_text(encoding='utf-8'))['uninstalled'])

    def test_hook_write_failure_rolls_back_install(self):
        self.codex.mkdir()
        original = json.dumps({'hooks': {'Stop': [
            {'hooks': [{'type': 'command', 'command': 'echo keep'}]}]}}, indent=2)
        self.hook.write_text(original, encoding='utf-8')
        before = self.hook.read_bytes()
        module = load_installer()
        with patch.object(module, 'change', side_effect=OSError('simulated config lock')):
            with self.assertRaises(RuntimeError):
                module.install(self.home, self.codex, python=Path(sys.executable),
                               backup_root=self.codex / 'backups/goal-await-user',
                               hook_shell='cmd')
        self.assertEqual(self.hook.read_bytes(), before)
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.record.exists())
        stages = list(self.skill.parent.glob('.goal-await-user-stage-*')) if self.skill.parent.exists() else []
        self.assertEqual(stages, [])


if __name__ == '__main__':
    unittest.main()
