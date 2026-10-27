# Feedback and contributions

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

See the [README](README.md) for usage, the [project installation guide](docs/PROJECT_INSTALL.md) for project setup, and the [AI installation and usage guide](docs/AI_GUIDE.md) for AI assistance.

## Reporting a problem

Include the version or commit, operating system, Python and Codex versions, installation scope, current shell, and expected versus actual behavior. Provide the shortest reproduction steps and only the necessary error details with private information removed. For automatic continuation issues, distinguish native Stop or heartbeat events from manually sent messages asking the AI to continue.

Do not upload login files, complete user configuration, configuration backups, full conversations, private paths, chat IDs, or project data. If a goal or scheduled task is still running, include its current status.

## Changes and validation

Keep runtime dependencies limited to the Python standard library, using native Stop hooks, heartbeat tasks, and the current chat's model. Separate runtime state from installed Skill files. Installation and uninstallation should preserve other hooks and user modifications.

Offline tests run in isolated directories without calling models or creating real scheduled tasks. From the repository directory, run:

```powershell
python -X utf8 -B -m unittest discover -s tests -v
```

Choose validation appropriate to the change. When checking real automatic continuation, record what you actually observed. Offline tests and manual continuation are not evidence that native automatic execution passed. Real installations, tasks, and resource use must stay within the tester's authorization.

Before committing, review the diff and file list. Exclude installed copies, runtime records, project files, and configuration backups. Public documentation should contain user-facing explanations and operating guides.

This project uses the [MIT License](LICENSE). Before contributing, confirm that you have the right to provide the material under this license, and retain applicable copyright and permission notices.
