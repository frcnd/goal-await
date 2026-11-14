# Goal-await

**English** | [简体中文](README.zh-CN.md)

Keep Codex working toward a goal in the current desktop chat. When work needs to wait, schedule a check and resume when the condition is met, then finish after verifying the result. Supports starting, waiting, pausing, resuming, and completing goals.

Goal-await combines a **Skill, local state scripts, and a fixed Stop hook**. It uses native Codex scheduled tasks to return to the same chat. Once installed, new goals need no hook reconfiguration. Installation and runtime scripts use only the Python standard library.

The current public preview is `0.4.0-preview`, designed for Codex. Validation currently covers Windows desktop; other platforms, other failure classes, recovery across restarts, and long unattended runs have not been validated. See [CHANGELOG.md](CHANGELOG.md) for version history.

## Usage

After installation and reloading, describe the complete goal in your project chat, for example:

```text
$goal-await Run the experiment, wait for the results, verify them, and generate a report.
$goal-await pause
$goal-await resume
$goal-await complete
```

Usually, you only need to state the goal once; the AI handles waiting and completion as work progresses. Include the expected outputs, acceptance criteria, and allowed resources. When waiting is needed, provide the result or log location. You do not need to repeat information already available in the chat.

Before using `wait`, the agent must verify who will make progress and record the evidence with the required `--progress-evidence` option. It waits only when work has already started, a request has been explicitly accepted, or a known event will occur independently. If another conversation or person must act and the request has not been issued or accepted, the goal is marked `blocked`, its scheduled check is paused, and the reply states who must do what and how the block can be cleared. Monitoring does not dispatch the request. Resuming a blocked goal returns to work for reassessment. An older wait without progress evidence stops for `NEEDS_REVIEW`.

Each chat manages one unfinished goal at a time. Model selection remains with the host and user configuration; a manual switch or host fallback does not rebind the goal or restore spent recovery attempts. Automatic progress stops with an explanation when the check count, time limit, or continuation budget is exhausted. `$goal-await` invokes a Skill. It maintains its own state separately from native Goal and does not add a built-in `/goal` command.

Before continuing after a Stop, Goal-await checks whether the turn produced new business tool activity. A text-only manual turn receives one corrective continuation; if no new activity follows, automatic continuation stops for review. It does not prompt again while the chat is in plan mode or while a visible wait is active. Continuation prompts still include the complete objective, and the three-continuation limit for a host turn still applies.

The Skill adds `--recover-errors` by default when it starts a new goal; direct CLI use is opt-in. Recovery defaults to two attempts at two-minute intervals. Configure it with `--recovery-max-attempts` (1 through 5) and `--recovery-interval-minutes` (1 through 60).

## Exception recovery

Recovery handles a finished assistant turn only when the current chat transcript contains one of these exact terminal errors, matched without regard to case:

- `Incomplete response returned, reason: max_output_tokens`
- `stream disconnected before completion`
- `response stream disconnected before completion`

It does not infer failure from inactivity, and it does not resubmit arbitrary business work. Before continuing, the Skill checks the original chat, workspace, automation, prior turn, interruption state, recovery count, and whether the previous continuation was acknowledged. It resumes from saved artifacts and current context, checking side effects before doing any unfinished step. Each failed turn may receive at most one recovery admission.

Recovery uses a one-shot native `RDATE` task and the same automation ID as a later waiting task. On first use, execute the returned effects, read the actual ID from the native receipt, and bind that ID. Later updates replace that task without changing its ID. Execute effects returned by an acknowledged continuation as well; a JSON effect is a pending instruction until the host operation succeeds. The automation payload carries the same target chat but does not override model selection.

Model selection remains with the host and user configuration. A manual switch or host fallback can continue through waiting, continuation acknowledgement, and bounded recovery without rebinding the goal or resetting the remaining recovery budget. Any model recorded at start is diagnostic only. User interruption, an unknown error, an unsupported or malformed transcript, exhausted attempts, or an unacknowledged delivery stops automatic recovery. If the selected model fails before the first recovery gate or before the next one-shot is armed, that single scheduled wake is already spent and the user must continue manually.

Stop continuation now uses the activity check described above. Continuation prompts still carry the complete objective, and the per-turn Stop limit remains three continuations; exception recovery has its own default budget of two and does not consume Stop continuations.

Goals created without recovery fields keep the old behavior. To upgrade from an earlier version, pause the goals and native timers, uninstall that installed version, then install `0.4.0-preview`, verify trust, and reload. Unfinished goals do not restart automatically. After an old RUNNING goal has been manually resumed, call `recovery-enable` in that same business chat and execute the returned effects to add recovery protection.

The `0.2.0-preview` recovery work was validated with 38 offline tests covering the user and project installers, lifecycle behavior, recovery gates, duplicate handling, attempt counts, interruption and model guards. The Windows desktop scheduler at `26.1103.4239.0` with core `0.167.0-alpha.3.2` was inspected. In that release, one real Kimi K3.1-Pro `max_output_tokens` failure was observed through a native heartbeat and same-chat, same-model recovery admission 1 of 2, followed by an acknowledged business continuation. This does not establish recovery for other failure classes, reboots, or end-to-end business or scientific validity. The premature-stream cases were validated offline only. The Stop activity check, host-selected model behavior, and wait admission have only offline validation; live runtime acceptance remains pending; they do not extend that historical recovery result.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for issue reports and development checks. See [CHANGELOG.md](CHANGELOG.md) for release history. Share only the necessary information with private details removed.

This project uses the [MIT License](LICENSE). Use, modification, commercial use, and redistribution are allowed, provided the copyright and permission notices are retained.
