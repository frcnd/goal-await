# Goal-await

**English** | [简体中文](README.zh-CN.md)

Keep Codex working toward a goal in the current desktop chat. When work needs to wait, schedule a check and resume when the condition is met, then finish after verifying the result. Supports starting, waiting, pausing, resuming, and completing goals.

Goal-await combines a **Skill, local state scripts, and a fixed Stop hook**. It uses native Codex scheduled tasks to return to the same chat. Once installed, new goals need no hook reconfiguration. Installation and runtime scripts use only the Python standard library.

The first public release is `0.1.0-preview`, designed for Codex. Validation currently covers Windows desktop. Other platforms, recovery across restarts, and long unattended runs have not been validated.

## Usage

After installation and reloading, describe the complete goal in your project chat, for example:

```text
$goal-await Run the experiment, wait for the results, verify them, and generate a report.
$goal-await pause
$goal-await resume
$goal-await complete
```

Usually, you only need to state the goal once; the AI handles waiting and completion as work progresses. Include the expected outputs, acceptance criteria, and allowed resources. When waiting is needed, provide the result or log location. You do not need to repeat information already available in the chat.

Each chat manages one unfinished goal at a time, and checks use that chat's current model. Automatic progress stops with an explanation when the check count, time limit, or continuation budget is exhausted. `$goal-await` invokes a Skill. It maintains its own state separately from native Goal and does not add a built-in `/goal` command.

## Automatic selection or explicit invocation

By default, the AI can select Goal-await when it considers the task suitable. Installation alone does not start a goal; the current chat is bound to a goal when the Skill's start action runs.

If you prefer to explicitly start Goal-await every time, edit [skill/goal-await/agents/openai.yaml](skill/goal-await/agents/openai.yaml) in this repository **before installation**. Keep the existing `interface` and add this at the same level:

```yaml
policy:
  allow_implicit_invocation: false
```

Then install as described below and invoke it explicitly with `$goal-await`. This Codex [invocation policy](https://learn.chatgpt.com/docs/build-skills#optional-metadata) disables automatic selection based on the task while preserving explicit invocation. It does not restrict the AI's permission to read files.

For an existing installation, follow the update procedure below to uninstall it, then edit the repository source and reinstall. Avoid editing the installed copy directly: the installer verifies file hashes, so modified copies must be preserved and reviewed before uninstalling. This setting does not pause goals that have already started.

## Installation

Requires Python 3.11+ and a signed-in Codex desktop environment that supports local Python execution, Stop hooks, and native same-chat heartbeat tasks. The host must provide a real `CODEX_THREAD_ID` and the `automation_update` interface.

| Scope | Use case | Instructions |
|---|---|---|
| User | Share one installation across projects | Steps below |
| Project | Enable it only in a selected project | [Project installation guide](docs/PROJECT_INSTALL.md) |

Choose one scope to avoid enabling both installations in the same project. Copying Skill files alone does not generate runtime paths or register the hook; use this repository's installer.

### User installation

Download or clone this repository, then run these commands from its directory. Review the plan and verify the paths and command before installing:

```powershell
python tools/install_user.py plan
python tools/install_user.py install
```

On Windows, the default hook command targets PowerShell. If the Codex chat actually uses CMD, add `--hook-shell cmd` to both commands. `python` must resolve to a working Python 3.11+ installation; use its full path if needed.

Default locations are listed below. When `CODEX_HOME` is unset, it defaults to `~/.codex`.

| Content | Location |
|---|---|
| Skill and scripts | `~/.agents/skills/goal-await/` |
| Fixed Stop hook | `$CODEX_HOME/hooks.json` |
| Goal state and installation record | `$CODEX_HOME/goal-await/` |
| Configuration backups | `$CODEX_HOME/backups/goal-await-user/` |

The plan writes no files. Installation backs up existing configuration and merges its own hook while preserving other hooks. It does not change your login, model, or provider. The installed copy is self-contained and no longer depends on the repository's location.

**Trust and reload are still required after installation:**

1. In the Codex hooks interface or CLI `/hooks`, find the Goal-await Stop from the user configuration. Verify that its command matches the plan and trust only that definition.
2. Open a new chat, or reload configuration normally while the existing chat is idle, then use `$goal-await`.

Writing the files does not mean an already-open chat has loaded them. The project helper `tools/trust.py --project` does not apply to user installations. If the runtime cannot write its state directory, use the host's permission flow to authorize only the required directory.

Inspect the installation record at any time:

```powershell
python tools/install_user.py inspect
```

The installer stops if a same-named Skill already exists. Check its origin first. Use the corresponding installer to remove a copy installed by this package; back up copies from other sources before moving them out of the installation location.

### Updating or uninstalling

First pause or complete all goals using this installation, and confirm in the native task interface that their scheduled tasks are paused or deleted. Then run:

```powershell
python tools/install_user.py uninstall --timers-paused
```

Uninstallation removes this package's unmodified installed copy and its own hook, preserving goal records, task outputs, and other hooks. It stops if files have changed so you can preserve and review the changes. An old trust entry may remain; remove only the exact entry after reviewing it. Do not overwrite newer configuration with an entire old backup.

To update, uninstall first, then use the new version to plan, install, review trust, and reload. Unfinished goals do not restart automatically. For project uninstall commands, see the [project installation guide](docs/PROJECT_INSTALL.md).

## Ask AI to help with installation

Give Codex the [AI installation and usage guide](docs/AI_GUIDE.md) and tell it whether to install for the current user or for a specific project. That guide supports installation and usage assistance; the installed Skill defines the runtime workflow.

## Feedback and contributions

See [CONTRIBUTING.md](CONTRIBUTING.md) for issue reports and development checks. Share only the necessary information with private details removed.

This project uses the [MIT License](LICENSE). Use, modification, commercial use, and redistribution are allowed, provided the copyright and permission notices are retained.
