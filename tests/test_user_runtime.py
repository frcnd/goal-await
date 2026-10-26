"""Offline user-scope bindings. Fake IDs, no native timers or desktop writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

REPO=Path(__file__).resolve().parents[1]
CHAT='11111111-1111-1111-1111-111111111111'


class UserRuntime(unittest.TestCase):
    def test_one_install_separate_chats_and_workspace_gate(self):
        base=REPO/'.test-work';base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temp:
            root=Path(temp).resolve();home=root/'home';home.mkdir()
            projects=[root/'a',root/'b']
            for p in projects:
                p.mkdir()
                (p/'.git').mkdir()  # Independent fixture repo; do not inherit this source repo's installed Skill.
            def run(script,args,cwd,chat=CHAT,ok=True,input=None):
                p=subprocess.run([sys.executable,'-X','utf8',str(script),*map(str,args)],
                    cwd=cwd,env=dict(os.environ,CODEX_THREAD_ID=chat),input=input,
                    capture_output=True,text=True,encoding='utf-8',timeout=20)
                if ok:self.assertEqual(p.returncode,0,p.stderr)
                else:self.assertNotEqual(p.returncode,0,p.stdout)
                return json.loads(p.stdout) if ok else p
            codex=home/'.codex'
            run(REPO/'tools/install_user.py',['install','--home',home,'--codex-home',codex],projects[0])
            skill=home/'.agents/skills/goal-await';entry=skill/'scripts/goal_entry.py'
            hook=codex/'hooks.json';before=hook.read_bytes()
            first=run(entry,['start','--objective','offline a','--workspace',projects[0]],projects[0])
            other=str(uuid.UUID(int=2))
            self.assertFalse(run(entry,['status'],projects[1],other)['active'])
            second=run(entry,['start','--objective','offline b'],projects[1],other)
            self.assertNotEqual(first['run_id'],second['run_id'])
            self.assertEqual(run(entry,['status'],projects[0])['run_id'],first['run_id'])
            self.assertEqual(run(entry,['status'],projects[1],other)['run_id'],second['run_id'])
            event=dict(hook_event_name='Stop',session_id=CHAT,turn_id='offline',cwd=str(projects[1]))
            stop=skill/'scripts/goal_await_stop.py'
            self.assertEqual(run(stop,['--bindings',codex/'goal-await/bindings'],projects[1],input=json.dumps(event)),{})
            event['cwd']=str(projects[0])
            self.assertEqual(run(stop,['--bindings',codex/'goal-await/bindings'],projects[0],input=json.dumps(event))['decision'],'block')
            for path,chat in zip(projects,[CHAT,other]):
                run(entry,['complete','--reason','offline binding check'],path,chat)
                self.assertFalse((path/'.goal-await').exists())
                self.assertFalse((path/'.codex').exists())
            run(entry,['start','--objective','mismatched cwd','--workspace',projects[1]],projects[0],ok=False)
            for name in ('goal-await', 'goal-lite'):
                local_skill=projects[0]/'.agents/skills'/name
                local_skill.mkdir(parents=True)
                refused=run(entry,['start','--objective','duplicate project scope'],projects[0],ok=False)
                self.assertIn(name,refused.stderr)
                local_skill.rmdir()
            self.assertEqual(hook.read_bytes(),before)
            run(REPO/'tools/install_user.py',['uninstall','--home',home,'--codex-home',codex],projects[0])


if __name__=='__main__':
    unittest.main()
