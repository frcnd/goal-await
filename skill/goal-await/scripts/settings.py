"""Installation-local paths, generated once by an explicit installer."""
import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
try:
    INSTALL = json.loads((SKILL_ROOT/'installation.json').read_text(encoding='utf-8'))
except FileNotFoundError:
    raise RuntimeError('Skill is not installed. Use the project or user installer from the release repository.') from None
SCOPE = INSTALL.get('scope', 'project')
if SCOPE == 'project':
    WORKSPACE = Path(INSTALL['workspace']).resolve()
    EXPECTED_SKILL = WORKSPACE/'.agents/skills/goal-await'
    DATA = WORKSPACE/'.goal-await'
elif SCOPE == 'user':
    WORKSPACE = Path.cwd().resolve()
    EXPECTED_SKILL = Path(INSTALL['skill_root']).resolve()
    DATA = Path(INSTALL['data_directory']).resolve()
else:
    raise RuntimeError('Unknown installation scope')
if SKILL_ROOT != EXPECTED_SKILL:
    raise RuntimeError('Installed Skill was moved; reinstall for this project before use')
