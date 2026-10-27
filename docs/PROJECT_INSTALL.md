# Goal-await project-level installation

**English** | [简体中文](PROJECT_INSTALL.zh-CN.md)

Project-level installation enables Goal-await only for a specified project: it writes that project's own Skill, Stop hook, ignore rules, and runtime state. For cross-project use, see the user-level installation in [README](../README.md); if an AI installs or uses Goal-await on a user's behalf, also read the [AI installation and usage guide](AI_GUIDE.md).

For official interface background, see [Skill paths](https://learn.chatgpt.com/docs/build-skills) and [hooks and trust](https://learn.chatgpt.com/docs/hooks).

## Prerequisites and environment

- Use a source directory containing this project's [installer](../install.py) and `skill/goal-await/`. The Skill is copied to the target project during installation and does not depend on the download directory afterward.
- Use Python 3.11+; prepare an existing **target project directory** with an accurate path, and confirm that the project is writable.
- Use a local Codex desktop environment that is signed in and can load Skills, native Stop, and same-chat heartbeat. The current validation scope is Windows; other platforms have not been validated.
- Windows generates a PowerShell Stop command by default. When the actual host is CMD, add `--hook-shell cmd` to both `plan` and `install`; do not use offline results from another shell to infer that it will work.
- If the project already has `.gitignore` and `.codex/hooks.json`, they must be UTF-8 (BOM allowed); the hook file must also be valid JSON. If the encoding or structure does not match, preserve the original files and verify them first.
- Keep only one Goal-await installation per project. Project-level and user-level hooks stack; do not enable identically named Skills at the same time.

## Preview, install, and inspect

Run these commands from the Goal-await source directory; replace `python` with the actual Python 3.11+ executable:

```powershell
python install.py plan --project "C:\work\my-project"
python install.py install --project "C:\work\my-project"
python install.py inspect --project "C:\work\my-project"
```

`plan` shows the target paths, data directory, and exact Stop definition that will be written, without writing files; verify the Python path in the command. Review the plan before running `install`. `inspect` currently returns the same plan information and cannot replace a successful installation receipt or an actual hook check. See below for backup locations.

If the target project already has `.agents/skills/goal-await` or an installation record from this installer that has not been uninstalled, the installer refuses to overwrite it. Preserve a copy and state, verify the source, then use the corresponding installer to uninstall; do not directly delete copies of unknown origin.

To change the installation scope, stop the old goals and complete uninstallation first, then install the new scope. For nested projects, also check for existing Skills and hooks in parent directories to avoid enabling duplicates.

## Trust the exact Stop

A successful installation does not mean Stop is trusted. In a Codex session for the project, use `/hooks` to view the Goal-await Stop sourced from the project `.codex/hooks.json`, verify that the command exactly matches `handler.command` in the `plan` output, and trust only that definition. Do not skip trust because the Skill has loaded, and do not trust other hooks in bulk.

The optional [project trust helper](../tools/trust.py) applies only to project-level installations. By default, it displays the exact key, hash, status, and enablement state read-only; after verifying, add `--apply`:

```powershell
python tools/trust.py --project "C:\work\my-project" --codex "C:\path\to\codex.exe"
python tools/trust.py --project "C:\work\my-project" --codex "C:\path\to\codex.exe" --apply
```

This helper works through Codex App Server's hook catalog and configuration interfaces. It does not start a model run or task chat, copy login credentials, or disable trust for all hooks. It writes the key/hash for only this definition and backs up the user's `config.toml` outside the project. It does not apply to user-level installations; stop and use the host interface if the host interfaces differ, the project is untrusted, or concurrent configuration changes are detected.

## Reload

After trusting, start a new conversation or let the original conversation normally reload its configuration while idle. The fact that files were written, a Skill is visible, or an auxiliary process reloaded does not mean the current desktop session has loaded Stop. Do not archive or restart other working conversations. You can then enter `$goal-await start: ...` in the project.

## Write locations and backups

| Location | Contents |
|---|---|
| `.agents/skills/goal-await/` | Self-contained Skill, scripts, and installation metadata |
| `.codex/hooks.json` | Merged Stop definition, preserving other hooks |
| `.gitignore` | Appends `.goal-await/` and `.agents/skills/goal-await/` only when absent |
| `.goal-await/` | Bindings for the current conversation, goal state, events, and installation records |
| `~/.codex/backups/goal-await/` | Default timestamped backup outside the project; use `--backup-root` to choose another location outside the project |

The installer first runs preflight checks on existing hooks, `.gitignore`, and installation records, and backs them up before writing. If it fails partway through, it rolls back the content it can identify. The backup root cannot be inside the target project. Installation copies only files from this package; it does not write Codex login, model, provider, or task outputs. The optional trust helper has its own backup directory for user configuration.

## Updates and uninstallation

Before updating or uninstalling, complete or pause all related goals and confirm through the native interface that the timers have stopped. Then run:

```powershell
python install.py uninstall --project "C:\work\my-project" --timers-paused
```

Omit `--timers-paused` only when there really are no native tasks waiting to be stopped. The uninstaller refuses to proceed while a `RUNNING` or `WAITING` goal remains, the Skill has been modified, or the record does not match the exact hook. On success, it removes only the unmodified Skill from this installation and this one Stop, preserving state, old trust entries, and task files; an old trust entry will not run without a corresponding hook.

To update, uninstall first, then run `plan` and `install` from the new source version, verify the exact Stop again, and reload. Incomplete goals are not restarted automatically. If someone else has changed `.gitignore` since installation, do not overwrite it with the entire old backup; verify and remove only the two lines appended by this installation.

For the current supported scope and limitations, see [README](../README.md).
