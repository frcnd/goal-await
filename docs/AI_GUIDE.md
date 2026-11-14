# Goal-await AI installation and usage guide

**English** | [简体中文](AI_GUIDE.zh-CN.md)

This is an operational reference that the user has voluntarily given to an AI. It does not authorize starting a goal, modifying the environment, or expanding the scope of authorized work. The AI should first read the current request, [README](../README.md), and the applicable installation instructions; when the user explicitly asks to install or use Goal-await, follow the boundaries here and preserve authorization already given in the conversation.

This document cannot replace the installed Skill. At runtime, read the `SKILL.md` at its installation location and call its current scripts and action formats; do not create another state machine, timer, daemon, or parallel hook.

## Determine authorization and missing information

- If the user only provides this document, explain the capability and ask whether to proceed; this does not mean the user has agreed to install or start a goal.
- For an "install" request: when the installation scope (user-level or project-level) and the exact target path can be determined from the current request and context, directly review the plan, install, verify trust, and reload without mechanically asking again for each item.
- When necessary information such as the installation scope or exact target path cannot be determined, ask for the missing information together. Next steps within a scope the user has already authorized do not need repeated confirmation.
- For a "start/continue goal" request: preserve existing authorization and completed work, and continue toward the original goal. The installation scope does not expand permissions for files, accounts, remote jobs, or publishing.
- For scope changes, paid resources, messages to others, or new external writes, check whether the user has already authorized that action in the current context; do not infer unrelated authorization from earlier text.

## Choose the installation scope

Identify the target first: choose user-level when multiple projects on the same account need to share it; choose project-level when enabling only an exact project. Do not enable identically named Skills in both scopes, because the Stop hooks stack. See [PROJECT_INSTALL](PROJECT_INSTALL.md) for project-level details and [README](../README.md) for the complete user-level flow.

Verify the installer in the download directory, Python 3.11+, the target path, write permissions, any existing `.agents/skills/goal-await`, and any installation record. Real desktop use should match the planned user directory, `CODEX_HOME`, and shell. A Windows CMD host uses `--hook-shell cmd` for both preview and installation; PowerShell uses the default.

Run the applicable `plan` first and read the JSON. Show the user the exact paths and Stop definition that will be written; if the user has authorized the action and the plan has no conflicts, run `install`. Do not bypass the installer's scope checks by editing configuration files, copying an installation, or adding a second hook.

Native trust is still required after installation. For a project-level installation, run `/hooks` in the target project and verify the exact Stop from the project source; only project-level installations may use `tools/trust.py` as described in [PROJECT_INSTALL](PROJECT_INSTALL.md). A user-level installation must use `/hooks` with the user configuration source and cannot use the project helper as a user-level trust entry point. Trust only the Stop from the plan, then start a new idle conversation or reload normally; do not treat a process reload as a desktop reload.

Implicit Skill invocation is allowed by default, so the source YAML usually does not need editing. When the user explicitly asks to disable implicit invocation, follow the `allow_implicit_invocation: false` instructions in the README to modify the **source** `skill/goal-await/agents/openai.yaml`: do this before the first installation. If it is already installed, pause goals, confirm in the native interface that tasks have stopped, uninstall with the original installer, then modify the source and install again. Do not directly edit the installed copy, because its file checksum will no longer match the installation record and uninstallation will be refused.

After completing the operations in the installation document for the selected scope, report the actual write locations, backup locations, and the status of trust and reload. Do not start a goal as a side effect of a successful installation; starting requires a user request for a clear task goal.

## Run Goal-await

After installation and reload, when the user has requested progress toward a clear goal and the current invocation policy allows it, the AI may proactively choose Goal-await without requiring the user to name it every time. Merely reading this document, discussing the capability, or completing installation does not start a goal. `$goal-await` is a Skill invocation, not a new built-in `/goal`.

Use the current context to clarify the goal, inputs, expected artifacts, acceptance criteria, and resource budget, then read the installed `SKILL.md` and follow it. The Skill defines the specific actions for starting, waiting, pausing, resuming, and completing; this document does not define a separate runtime.

Advance only goals authorized in the current conversation, and respect user pauses and interruptions. Do not scan, rewrite, or restart goals in other conversations. Verify evidence for the complete goal before finishing. Writing local state alone does not mean a native timer operation succeeded; verify the actual tool receipt.

Before a Stop continuation, read the turn's business tool activity. File changes, commands, and valid tool results can support further work; plans, status narration, Goal-await control bookkeeping, and prior hook feedback do not count as progress. A text-only manual turn receives one corrective continuation. If the next check still finds no new activity, automatic continuation stops and the goal moves to review. Plan mode and a visible active wait suppress extra prompts. Keep the full objective in continuation prompts, and keep the three-continuation limit for each host turn. Activity is evidence about whether to continue, not proof that the complete goal has passed acceptance.

## Exception recovery

For a new goal, use the Skill's start workflow with `--recover-errors`; this is the Skill default. The CLI option remains explicit opt-in. The default is two attempts at two-minute intervals, configurable with `--recovery-max-attempts` from 1 through 5 and `--recovery-interval-minutes` from 1 through 60. If start cannot identify a supported active transcript, stop and explain that recovery was not enabled.

Execute every returned native automation effect and verify its receipt before describing the timer as armed. On first creation, read the actual automation ID from the native tool result and bind that exact ID. Recovery and later waiting use that one ID and a one-shot `RDATE` schedule. When an acknowledge result contains automation effects, execute those as well. A continuation JSON object is pending work until its acknowledge command returns `accepted: true`; then update the timer before doing business work. Do not send a message to the current chat or create a second timer.

Recovery admits only an exact terminal `max_output_tokens` message or one of the two exact premature-stream messages recognized by the packaged script. It does not infer failure from elapsed time and does not resubmit arbitrary business jobs. The gate checks the original chat and workspace, the automation marker, the prior and current turns, interruption state, remaining attempts, and delivery acknowledgement. Continue from saved artifacts and the latest context; inspect side effects before doing only the unfinished work.

Model selection remains with the host and user configuration. A manual switch or host fallback can continue through waiting, continuation acknowledgement, and bounded recovery without rebinding the goal or resetting the remaining recovery budget; any recorded model is diagnostic only. Stop recovery on user interruption, unknown errors, missing or unsupported transcript data, exhausted attempts, or an unacknowledged delivery. If the selected model fails before the first recovery gate or before the next one-shot is armed, report that the single wake was consumed and ask the user to continue manually.

For an old RUNNING goal after a manual resume, call `recovery-enable` in that same business chat and execute the returned effects. Existing goals without recovery fields keep their old behavior. Before upgrading from an earlier version, pause the goals and native timers, uninstall the current installation, then install and reload `0.3.1-preview`. Do not claim that existing goals restart automatically; they do not.

## Installation conflicts

If project-level and user-level installations coexist, a project contains an identically named Skill of unknown origin, or an installation record does not match the exact hook, stop the relevant goals and preserve the current state. After determining which installer created it, uninstall with that installer; do not bypass protections with a forced deletion or by rewriting hashes.

## Diagnostic boundaries

A `status` result with `active: false` only means the current conversation has not been bound to a goal; it is not an installation failure. If the user has explicitly requested a start and the goal is clear, call the installed `start`; otherwise explain that the goal to advance needs user confirmation.

When judging a command result, check the exit code, stdout JSON, and stderr together. If Windows reports `Failed to find real location of ...` while the exit code is 0 and the JSON is valid, do not conclude from that message that the interpreter does not exist. If the exit code is nonzero, an expected field is missing, or the JSON contains `error`, then check the exact path, UTF-8 encoding, permissions, installation scope, installation record, and hook definition, and preserve the original error.

When a Stop hook explicitly reports failure, do not claim that automatic continuation succeeded. First check whether the current conversation has a valid binding, whether the hook has been loaded, whether the exact definition is trusted, and whether the state directory is writable; when permission is needed, use the host's normal approval process and limit it to that directory.

For recovery, distinguish an admitted continuation from a request the host merely sent. A successfully created or updated native task needs its actual receipt and binding. Observe the gate result, execute ACK effects, and require `accepted: true` before business continuation. Report interruption, unsupported transcript format, exhausted attempts, or a consumed one-shot wake as stopping conditions rather than as proof that the business goal failed.

Refusals by `install.py uninstall` for a modified installation copy, or for a missing or duplicate Stop from this package, are protective measures; preserve the state, verify the source, and do not force deletion. If user-level and project-level installations both exist, verify them and uninstall the installation you no longer use; do not run both.

For the supported scope and limitations, see [README](../README.md). Verify the actual result of the current operation; do not treat a successful file copy or manual continuation as successful native automatic continuation.
