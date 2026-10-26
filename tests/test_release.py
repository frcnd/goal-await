"""Offline installation/lifecycle checks. No Codex, models, real timers or login."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
CHAT='11111111-1111-1111-1111-111111111111'


class Release(unittest.TestCase):
    def setUp(self):
        work=REPO/'.test-work';work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve()
        self.project=self.base/'project with spaces 中文';self.project.mkdir()
        self.backups=self.base/'backups'
        self.skill=self.project/'.agents/skills/goal-await'
        self.env=dict(os.environ,CODEX_THREAD_ID=CHAT,PYTHONUTF8='1')

    def run_py(self,path,*args,ok=True):
        p=subprocess.run([sys.executable,'-X','utf8',str(path),*map(str,args)],cwd=self.project,
                         env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        if ok:self.assertEqual(p.returncode,0,p.stderr)
        else:self.assertNotEqual(p.returncode,0,p.stdout)
        return json.loads(p.stdout) if ok and p.stdout else p

    def setup_skill(self):
        return self.run_py(REPO/'install.py','install','--project',self.project,
                           '--backup-root',self.backups,'--hook-shell','cmd')

    def entry(self,action,*args):
        return self.run_py(self.skill/'scripts/goal_entry.py',action,*args)

    def core(self,action,run_id,*args):
        return self.run_py(self.skill/'scripts/goal_await.py',action,'--run-id',run_id,*args)

    def test_plan_does_not_change_project(self):
        plan=self.run_py(REPO/'install.py','plan','--project',self.project)
        self.assertFalse(plan['modifies_user_config'])
        if os.name=='nt':self.assertTrue(plan['handler']['command'].startswith('& '))
        self.assertEqual(list(self.project.iterdir()),[])

    def test_legacy_install_requires_uninstall_before_renamed_install(self):
        legacy=self.project/'.agents/skills/goal-lite'
        legacy.mkdir(parents=True)
        refused=self.run_py(REPO/'install.py','install','--project',self.project,
                            '--backup-root',self.backups,ok=False)
        self.assertIn('goal-lite',refused.stderr)
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.backups.exists())
        legacy.rmdir()
        record=self.project/'.goal-lite/install.json'
        record.parent.mkdir()
        record.write_text(json.dumps({'uninstalled':False}),encoding='utf-8')
        self.run_py(REPO/'install.py','install','--project',self.project,
                    '--backup-root',self.backups,ok=False)
        self.assertFalse((self.project/'.codex/hooks.json').exists())
        record.write_text(json.dumps({'uninstalled':True}),encoding='utf-8')
        before=record.read_bytes()
        self.setup_skill()
        self.assertEqual(record.read_bytes(),before)

    @unittest.skipUnless(os.name=='nt','Windows PowerShell definition only')
    def test_windows_powershell_definition_launches_python_entry(self):
        pwsh=shutil.which('pwsh')
        if pwsh is None:self.skipTest('pwsh is not installed')
        self.run_py(REPO/'install.py','install','--project',self.project,
                    '--backup-root',self.backups,'--hook-shell','powershell')
        handler=json.loads((self.project/'.codex/hooks.json').read_text(encoding='utf-8'))['hooks']['Stop'][0]['hooks'][0]['command']
        self.assertTrue(handler.startswith('& '))
        p=subprocess.run([pwsh,'-NoLogo','-NoProfile','-NonInteractive','-Command',handler+' --help'],
                         cwd=self.project,env=self.env,capture_output=True,text=True,
                         encoding='utf-8',timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('goal_await_stop.py',p.stdout)

    def test_non_utf8_ignore_is_rejected_before_install(self):
        original='original-rule/\n'.encode('utf-16')
        (self.project/'.gitignore').write_bytes(original)
        self.run_py(REPO/'install.py','install','--project',self.project,'--backup-root',self.backups,ok=False)
        self.assertFalse(self.skill.exists())
        self.assertFalse((self.project/'.codex/hooks.json').exists())
        self.assertEqual((self.project/'.gitignore').read_bytes(),original)
        # A normal config-write failure must also leave no orphan installation.
        (self.project/'.gitignore').write_text('original-rule/\n',encoding='utf-8')
        spec=importlib.util.spec_from_file_location('release_installer',REPO/'install.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        with patch.object(installer,'change',side_effect=OSError('simulated config lock')):
            with self.assertRaises(RuntimeError):installer.install(self.project,Path(sys.executable),self.backups)
        self.assertFalse(self.skill.exists())
        self.assertFalse((self.project/'.codex/hooks.json').exists())
        self.setup_skill()  # Failed copy/commit must not lock out a later install.

    def test_relocated_short_lifecycle_and_fixed_hook(self):
        self.setup_skill()
        hook=self.project/'.codex/hooks.json';initial=hook.read_bytes()
        started=self.entry('start','--objective','finish result','--workspace',self.project)
        run_id=started['run_id']
        handler=json.loads(initial)['hooks']['Stop'][0]['hooks'][0]['command']
        event=dict(hook_event_name='Stop',session_id=CHAT,turn_id='one-turn',cwd=str(self.project))
        def stop():
            # Match native command_runner's raw outer quoting on Windows;
            # subprocess(list) would incorrectly backslash-escape inner quotes.
            command=('"'+os.environ.get('COMSPEC','cmd.exe')+'" /d /c "'+handler+'"') if os.name=='nt' else ['/bin/sh','-c',handler]
            p=subprocess.run(command,input=json.dumps(event),capture_output=True,text=True,encoding='utf-8',
                             cwd=self.project,env=self.env,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            return json.loads(p.stdout)
        self.assertEqual(stop()['decision'],'block')
        wait=self.entry('wait','--waiting-for','result','--completion','answer 42','--result-location','result.json')
        self.assertEqual(wait['effects']['automation']['mode'],'create')
        self.entry('bind','--automation-id','offline-unit-timer')
        self.assertEqual(self.entry('pause')['effects']['automation']['status'],'PAUSED')
        resumed=self.entry('resume');generation=str(resumed['generation'])
        self.assertEqual(stop(),{})
        admitted=self.core('check',run_id,'--generation',generation)
        report=self.core('report',run_id,'--generation',generation,'--observation-id',admitted['observation_id'],
                         '--task-state','completed','--observation-state','ok','--reason','unit evidence',
                         '--evidence','offline fixture answer 42')
        self.assertEqual(report['state'],'RUNNING')
        self.core('acknowledge',run_id,'--ticket',report['effects']['ticket'])
        self.entry('complete','--reason','unit accepted')
        self.assertEqual(stop(),{})
        new=self.entry('start','--objective','second goal','--workspace',self.project)
        self.assertNotEqual(run_id,new['run_id'])
        self.assertIn('second goal',stop()['reason'])
        self.assertEqual(hook.read_bytes(),initial)

    def test_uninstall_preserves_other_hooks_and_retains_state(self):
        cfg=self.project/'.codex';cfg.mkdir()
        existing={'hooks':{'PreToolUse':[{'hooks':[{'type':'command','command':'echo existing'}]}]}}
        (cfg/'hooks.json').write_text(json.dumps(existing),encoding='utf-8')
        (self.project/'.gitignore').write_text('original-rule/\n',encoding='utf-8')
        self.setup_skill()
        data=json.loads((cfg/'hooks.json').read_text())
        data['hooks']['Stop'].append({'hooks':[{'type':'command','command':'echo later-user-hook'}]})
        (cfg/'hooks.json').write_text(json.dumps(data),encoding='utf-8')
        self.run_py(REPO/'install.py','uninstall','--project',self.project)
        final=json.loads((cfg/'hooks.json').read_text())
        self.assertEqual(final['hooks']['PreToolUse'],existing['hooks']['PreToolUse'])
        self.assertEqual(final['hooks']['Stop'][0]['hooks'][0]['command'],'echo later-user-hook')
        self.assertFalse(self.skill.exists())
        self.assertTrue((self.project/'.goal-await/install.json').exists())
        self.assertEqual((self.project/'.gitignore').read_text(),'original-rule/\n')
        self.setup_skill()  # Uninstalled metadata does not prevent reinstall.
        snapshot=(cfg/'hooks.json').read_bytes()
        spec=importlib.util.spec_from_file_location('release_installer',REPO/'install.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        with patch.object(installer,'change',side_effect=OSError('simulated config lock')):
            with self.assertRaises(RuntimeError):installer.uninstall(self.project)
        self.assertTrue(self.skill.exists())
        self.assertEqual((cfg/'hooks.json').read_bytes(),snapshot)

    def test_existing_or_modified_skill_is_never_overwritten(self):
        self.setup_skill()
        hook=(self.project/'.codex/hooks.json').read_bytes()
        self.run_py(REPO/'install.py','install','--project',self.project,'--backup-root',self.backups,ok=False)
        file=self.skill/'scripts/goal_entry.py';file.write_text(file.read_text()+'\n# user edit\n',encoding='utf-8')
        self.run_py(REPO/'install.py','uninstall','--project',self.project,ok=False)
        self.assertEqual((self.project/'.codex/hooks.json').read_bytes(),hook)
        self.assertIn('# user edit',file.read_text())


if __name__=='__main__':
    unittest.main()
