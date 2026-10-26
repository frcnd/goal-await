"""Plan, install, inspect, and remove the user-scoped Goal-await Skill.

This installer only manages the selected HOME Skill and Codex hook/data paths.
It never writes Codex config.toml or trust state.  Plan and inspect are read-only.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'skill/goal-await'
SCHEMA_VERSION = 1
TRUST_NOTE = (
    'After installation, review the exact Stop handler through /hooks and '
    'trust that one definition. This installer does not write config.toml, '
    'trust state, or any other Codex configuration.'
)

# Reuse the project installer's checked file operations instead of maintaining
# a second hash/write/rollback implementation.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import install as project_install

digest = project_install.digest
atomic = project_install.atomic
change = project_install.change
rollback = project_install.rollback
remove_owned_tree = project_install.remove_owned_tree


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _is_cache(path):
    return '__pycache__' in path.parts or path.suffix == '.pyc'


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _managed_path(raw, root, label):
    """Reject linked path components and keep the path fixed below root."""
    raw = Path(raw)
    current = root
    for part in raw.relative_to(root).parts:
        current /= part
        if _is_link(current):
            raise ValueError(f'{label} must not contain a symbolic link or junction')
    resolved = raw.resolve()
    if resolved != raw or not resolved.is_relative_to(root):
        raise ValueError(f'{label} resolves outside its allowed directory')
    return resolved


def resolve_paths(home=None, codex_home=None):
    """Return canonical managed paths and reject targets outside their roots."""
    home_path = Path(home).expanduser() if home is not None else Path.home()
    home_path = home_path.resolve()
    if not home_path.is_dir():
        raise ValueError('Home must be an existing directory')
    if codex_home is None:
        configured = os.environ.get('CODEX_HOME')
        codex_path = Path(configured).expanduser() if configured else home_path / '.codex'
    else:
        codex_path = Path(codex_home).expanduser()
    codex_path = codex_path.resolve()

    skill = _managed_path(home_path / '.agents/skills/goal-await', home_path, 'Skill')
    skill_parent = skill.parent
    hook = _managed_path(codex_path / 'hooks.json', codex_path, 'hook file')
    data = _managed_path(codex_path / 'goal-await', codex_path, 'data directory')
    record = _managed_path(data / 'install.json', data, 'installation record')
    return {
        'home': home_path,
        'codex_home': codex_path,
        'skill_parent': skill_parent,
        'skill': skill,
        'hook_file': hook,
        'data_directory': data,
        'record': record,
    }


def _python(value=None):
    python = Path(value if value is not None else sys.executable).expanduser().resolve()
    if not python.is_file():
        raise ValueError('Python executable does not exist')
    return python


def _backup_root(locations, value=None):
    root = (Path(value).expanduser() if value is not None else
            locations['codex_home'] / 'backups/goal-await-user').resolve()
    skill = locations['skill']
    if root == skill or root.is_relative_to(skill):
        raise ValueError('Backup root must be outside the installed Skill')
    return root


def handler_definition(locations, python, hook_shell=None):
    args = [str(python), '-X', 'utf8',
            str(locations['skill'] / 'scripts/goal_await_stop.py'),
            '--bindings', str(locations['data_directory'] / 'bindings'),
            '--max-continuations', '3']
    return project_install.command_handler(args, hook_shell=hook_shell)


def _read_hook(path):
    if not path.exists():
        return None, {'hooks': {}}
    try:
        raw = path.read_bytes()
        config = json.loads(raw.decode('utf-8-sig'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Existing hook file is not valid UTF-8 JSON: {exc}') from None
    _validate_stop_shape(config)
    return raw, config


def _validate_stop_shape(config):
    if not isinstance(config, dict):
        raise ValueError('Existing hook file must contain a JSON object')
    hooks = config.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Existing hooks field must be an object')
    groups = hooks.get('Stop', [])
    if not isinstance(groups, list):
        raise ValueError('Existing Stop hook groups must be a list')
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
            raise ValueError('Existing Stop hook groups are malformed')


def _stop_handlers(config):
    _validate_stop_shape(config)
    return [handler for group in config.get('hooks', {}).get('Stop', [])
            for handler in group.get('hooks', [])]


def _active_record(record):
    if not record.exists():
        return None
    try:
        value = json.loads(record.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Existing installation record is invalid: {exc}') from None
    if not isinstance(value, dict):
        raise ValueError('Existing installation record must be an object')
    return value


def plan(home=None, codex_home=None, python=None, backup_root=None, hook_shell=None):
    locations = resolve_paths(home, codex_home)
    python = _python(python)
    backup = _backup_root(locations, backup_root)
    handler = handler_definition(locations, python, hook_shell)
    record = _active_record(locations['record'])
    return {
        'action': 'plan',
        'scope': 'user',
        'home': str(locations['home']),
        'codex_home': str(locations['codex_home']),
        'skill': str(locations['skill']),
        'hook_file': str(locations['hook_file']),
        'data_directory': str(locations['data_directory']),
        'record': str(locations['record']),
        'python': str(python),
        'backup_root': str(backup),
        'backup': str(backup / '<timestamp>-<random>'),
        'backup_directory_pattern': str(backup / '<timestamp>-<random>'),
        'handler': handler,
        'hook_exists': locations['hook_file'].exists(),
        'skill_exists': locations['skill'].exists(),
        'installation_exists': bool(record and not record.get('uninstalled')),
        'side_effects': 'none',
        'modifies_user_config': False,
        'writes_config_toml_or_trust': False,
        'trust': TRUST_NOTE,
    }


def _render_skill(stage, skill, data_directory, python):
    if not SOURCE.is_dir():
        raise ValueError(f'Skill source is missing: {SOURCE}')
    replacements = {
        '{{PYTHON_JSON}}': json.dumps(python.as_posix(), ensure_ascii=False),
        '{{ENTRY_JSON}}': json.dumps((skill / 'scripts/goal_entry.py').as_posix(), ensure_ascii=False),
        '{{SCOPE_JSON}}': json.dumps('user', ensure_ascii=False),
    }
    stage.mkdir(parents=True, exist_ok=False)
    for source in sorted(SOURCE.rglob('*')):
        if source.is_symlink():
            raise ValueError(f'Skill source contains a symbolic link: {source}')
        if not source.is_file() or _is_cache(source.relative_to(SOURCE)):
            continue
        relative = source.relative_to(SOURCE)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding='utf-8')
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        target.write_bytes(text.encode('utf-8'))
    installation = {
        'schema_version': SCHEMA_VERSION,
        'scope': 'user',
        'skill_root': str(skill),
        'data_directory': str(data_directory),
        'python': str(python),
    }
    return installation


def _skill_files(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Installed Skill directory is missing or unsafe')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Installed Skill contains a symbolic link')
        if path.is_file() and not _is_cache(path.relative_to(root)):
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def _ownership(root, expected):
    if not isinstance(expected, dict):
        raise ValueError('Installation record has no valid Skill file hashes')
    safe_expected = {}
    for name, value in expected.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or not value:
            raise ValueError('Installation record contains an unsafe Skill file path')
        safe_expected[relative.as_posix()] = value
    actual = _skill_files(root)
    return {
        'matches': actual == safe_expected,
        'missing': sorted(set(safe_expected) - set(actual)),
        'extra': sorted(set(actual) - set(safe_expected)),
        'modified': sorted(name for name in set(actual) & set(safe_expected)
                           if actual[name] != safe_expected[name]),
        'files': actual,
    }


def _backup_directory(backup_root):
    backup_root.mkdir(parents=True, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = backup_root / name
    backup.mkdir(exist_ok=False)
    return backup


def _assert_current(path, expected, label):
    if digest(path) != expected:
        raise ValueError(f'{label} changed during installation; not overwritten')


def _cleanup_path(path, parent, errors):
    try:
        if path.exists() or path.is_symlink():
            remove_owned_tree(path, parent)
    except Exception:
        errors.append(str(path))


def install(home=None, codex_home=None, python=None, backup_root=None, hook_shell=None):
    locations = resolve_paths(home, codex_home)
    project_install.check_legacy_install(
        locations['home'] / '.agents/skills/goal-lite',
        locations['codex_home'] / 'goal-lite/install.json')
    python = _python(python)
    backup_root = _backup_root(locations, backup_root)
    skill = locations['skill']
    hook = locations['hook_file']
    record_path = locations['record']
    if skill.exists() or skill.is_symlink():
        raise ValueError('Existing Skill found at the user Scope path; inspect and preserve it before reinstalling')
    previous_record = _active_record(record_path)
    if previous_record and not previous_record.get('uninstalled'):
        raise ValueError('An existing user installation is recorded; inspect or uninstall it first')

    original_hook, config = _read_hook(hook)
    before_hook = digest(hook)
    handler = handler_definition(locations, python, hook_shell)
    if handler in _stop_handlers(config):
        raise ValueError('The exact Goal-await Stop handler already exists without an owned installation')
    groups = config.setdefault('hooks', {}).setdefault('Stop', [])
    group = {'hooks': [handler]}
    groups.append(group)

    backup = _backup_directory(backup_root)
    if original_hook is not None:
        (backup / 'hooks.json').write_bytes(original_hook)
    if record_path.exists():
        shutil.copyfile(record_path, backup / 'previous-install.json')

    skill.parent.mkdir(parents=True, exist_ok=True)
    if not skill.resolve().is_relative_to(locations['home']) or skill.is_symlink():
        raise ValueError('Skill staging would escape the selected home')
    if skill.exists():
        raise ValueError('A same-named Skill appeared during installation')
    stage = skill.parent / ('.goal-await-user-stage-' + uuid.uuid4().hex)
    stage_resolved = stage.resolve()
    if stage_resolved.parent != skill.parent.resolve() or not stage_resolved.is_relative_to(locations['home']):
        raise ValueError('Skill staging path is outside the selected home')

    changes = []
    moved = False
    skill_hashes = {}
    try:
        installation = _render_skill(stage, skill, locations['data_directory'], python)
        atomic(stage / 'installation.json', installation)
        skill_hashes = _skill_files(stage)
        _assert_current(hook, before_hook, 'Hook file')
        if skill.exists():
            raise ValueError('A same-named Skill appeared during installation')
        os.replace(stage, skill)
        moved = True
        change(hook, _json_bytes(config), changes)
        result = {
            'schema_version': SCHEMA_VERSION,
            'scope': 'user',
            'skill': str(skill),
            'hook_file': str(hook),
            'data_directory': str(locations['data_directory']),
            'handler': handler,
            'backup': str(backup),
            'skill_files': skill_hashes,
            'hook_existed': original_hook is not None,
            'uninstalled': False,
            'trusted': False,
        }
        change(record_path, _json_bytes(result), changes)
    except Exception as exc:
        errors = rollback(changes)
        if moved:
            try:
                owned = _ownership(skill, skill_hashes)
                if not owned['matches']:
                    raise ValueError('Installed Skill changed; not removed')
                remove_owned_tree(skill, skill.parent)
            except Exception:
                errors.append(str(skill))
        _cleanup_path(stage, skill.parent, errors)
        raise RuntimeError(
            f'User installation failed: {exc}; backup={backup}; '
            f'manual recovery paths={errors}') from None
    return {
        'installed': True,
        'scope': 'user',
        'skill': str(skill),
        'hook_file': str(hook),
        'data_directory': str(locations['data_directory']),
        'backup': str(backup),
        'handler': handler,
        'trust_required': True,
        'trust': TRUST_NOTE,
    }


def inspect(home=None, codex_home=None, python=None, backup_root=None, hook_shell=None):
    locations = resolve_paths(home, codex_home)
    record = _active_record(locations['record'])
    result = {
        'action': 'inspect',
        'scope': 'user',
        'record': str(locations['record']),
        'skill': str(locations['skill']),
        'hook_file': str(locations['hook_file']),
        'data_directory': str(locations['data_directory']),
        'installed': False,
        'uninstalled': None,
        'ownership_ok': None,
        'hook_owned': None,
    }
    if record is None:
        return result
    result.update({
        'installed': not bool(record.get('uninstalled')),
        'uninstalled': bool(record.get('uninstalled')),
        'backup': record.get('backup'),
        'handler': record.get('handler'),
        'hook_existed': record.get('hook_existed'),
        'schema_version': record.get('schema_version'),
    })
    if record.get('skill') and Path(record['skill']).resolve() == locations['skill']:
        try:
            owned = _ownership(locations['skill'], record.get('skill_files'))
            result['ownership_ok'] = owned['matches']
            result['ownership_differences'] = {
                key: owned[key] for key in ('missing', 'extra', 'modified')
                if owned[key]
            }
        except Exception as exc:
            result['ownership_ok'] = False
            result['ownership_error'] = str(exc)
    else:
        result['ownership_ok'] = False
        result['ownership_error'] = 'Recorded Skill path does not match the user Scope path'
    try:
        _, config = _read_hook(locations['hook_file'])
        result['hook_owned'] = _stop_handlers(config).count(record.get('handler')) == 1
    except Exception as exc:
        result['hook_owned'] = False
        result['hook_error'] = str(exc)
    return result


def _check_goals(data_directory, timers_paused):
    runs = data_directory / 'runs'
    if not runs.exists():
        return
    for path in sorted(runs.glob('*/state.json')):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'Goal state is not readable: {path}: {exc}') from None
        if state.get('state') in ('RUNNING', 'WAITING'):
            raise ValueError('Pause or finish active goals before uninstalling')
        if state.get('automation_id') and not timers_paused:
            raise ValueError('Verify native timers are paused, then add --timers-paused; local state cannot confirm this')


def _remove_handler(config, handler):
    groups = config.get('hooks', {}).get('Stop', [])
    matches = [(group, index) for group in groups
               for index, candidate in enumerate(group.get('hooks', []))
               if candidate == handler]
    if len(matches) != 1:
        raise ValueError('Owned Stop handler is missing or duplicated; inspect the hook file manually')
    group, index = matches[0]
    del group['hooks'][index]
    if not group['hooks']:
        config['hooks']['Stop'].remove(group)
    if not config['hooks']['Stop']:
        del config['hooks']['Stop']
    return config


def uninstall(home=None, codex_home=None, timers_paused=False):
    locations = resolve_paths(home, codex_home)
    skill = locations['skill']
    hook = locations['hook_file']
    record_path = locations['record']
    meta = _active_record(record_path)
    if meta is None:
        raise ValueError('No user installation record found')
    if meta.get('uninstalled'):
        return {
            'uninstalled': True,
            'already_uninstalled': True,
            'state_retained': True,
            'record': str(record_path),
        }
    if meta.get('schema_version') != SCHEMA_VERSION or meta.get('scope') != 'user':
        raise ValueError('Installation record does not belong to this user Scope installer')
    for key, expected, root in [
        ('skill', skill, locations['home']),
        ('data_directory', locations['data_directory'], locations['codex_home']),
        ('hook_file', hook, locations['codex_home']),
    ]:
        if not meta.get(key) or Path(meta[key]).resolve() != expected.resolve():
            raise ValueError(f'Installation record {key} does not match the managed path')
        if not expected.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Installation record {key} resolves outside its allowed directory')

    _check_goals(locations['data_directory'], timers_paused)
    owned = _ownership(skill, meta.get('skill_files'))
    if not owned['matches']:
        raise ValueError('Installed Skill changed; preserve and review the edits before uninstalling')
    if skill.is_symlink() or not skill.resolve().is_relative_to(locations['home']):
        raise ValueError('Unexpected Skill deletion target')

    _, config = _read_hook(hook)
    before_hook = digest(hook)
    config = _remove_handler(config, meta.get('handler'))
    stage = skill.parent / ('.goal-await-user-uninstall-' + uuid.uuid4().hex)
    if stage.exists() or stage.is_symlink():
        raise ValueError('Uninstall staging path already exists')
    if stage.resolve().parent != skill.parent.resolve():
        raise ValueError('Uninstall staging path is outside the Skill parent')
    os.replace(skill, stage)

    changes = []
    try:
        _assert_current(hook, before_hook, 'Hook file')
        hook_data = (None if not meta.get('hook_existed') and config == {'hooks': {}}
                     else _json_bytes(config))
        change(hook, hook_data, changes)
        meta['uninstalled'] = True
        change(record_path, _json_bytes(meta), changes)
    except Exception as exc:
        errors = rollback(changes)
        try:
            os.replace(stage, skill)
        except Exception:
            errors.append(str(stage))
        raise RuntimeError(
            f'User uninstall failed: {exc}; manual recovery paths={errors}') from None

    retained_copy = None
    try:
        remove_owned_tree(stage, skill.parent)
    except Exception:
        retained_copy = str(stage)
    return {
        'uninstalled': True,
        'scope': 'user',
        'state_retained': True,
        'record': str(record_path),
        'retained_copy': retained_copy,
        'trust': 'A trust entry may remain in user configuration. Review and remove only its exact key; this installer did not write or remove trust state.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'install', 'inspect', 'uninstall'], nargs='?', default='plan')
    parser.add_argument('--home', type=Path, help='User home (default: Path.home())')
    parser.add_argument('--codex-home', type=Path, help='Codex home (default: CODEX_HOME or home/.codex)')
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--backup-root', type=Path,
                        help='Backup root (default: codex-home/backups/goal-await-user)')
    parser.add_argument('--hook-shell', choices=['powershell', 'cmd'],
                        help='Windows Stop command shell (default: powershell)')
    parser.add_argument('--timers-paused', action='store_true',
                        help='Confirm native goal timers were checked and paused before uninstall')
    args = parser.parse_args()
    try:
        common = dict(python=args.python, backup_root=args.backup_root, hook_shell=args.hook_shell)
        if args.action == 'plan':
            result = plan(args.home, args.codex_home, **common)
        elif args.action == 'install':
            result = install(args.home, args.codex_home, **common)
        elif args.action == 'inspect':
            result = inspect(args.home, args.codex_home, **common)
        else:
            result = uninstall(args.home, args.codex_home, timers_paused=args.timers_paused)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
