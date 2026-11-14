# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

## 0.4.0-preview - 2026-11-14

- Require `wait --progress-evidence` to record work already started or explicitly accepted, or an independently occurring event. The field records a declaration; the agent must still verify the facts.
- Treat unissued external dependencies as blockers with concrete action needs. Observation can report `dependency` and pause monitoring; monitoring does not dispatch work on the user's behalf.
- Resume blocked goals into the work phase for reassessment. Returning to work clears the previous wait and observation; legacy waits without progress evidence enter `NEEDS_REVIEW`.
- Condense the Skill, guide, and observation rules while preserving admission, evidence, authorization, and reassessment requirements. Dependency tests use explicit coordination replies.
- Validated with all 92 offline tests on 2026-11-14, including wait admission, dependency reports, legacy-wait review, and installation-version checks. Live acceptance of the combined Stop, model-selection, and wait-admission changes remains pending.

Upgrade: pause goals and native timers, uninstall the previous version, then install, verify trust, and reload. Existing goals do not restart automatically. Before waiting again, reassess blocked goals and legacy waits and provide fresh `--progress-evidence`; do not reuse an unverified old wait.

## 0.3.1-preview - 2026-11-14

- Allow manual model changes and host fallback chains during recovery, waiting, acknowledgement, and Stop continuation. Model selection remains with the host and user configuration.
- Remove the model consistency gate without rebinding goals or resetting recovery attempts. Existing model snapshots are diagnostic only; interruption, chat, workspace, acknowledgement, and retry-budget checks remain in place.
- Validated with all 85 offline tests on 2026-11-14, covering model changes in recovery, waiting, acknowledgement, and Stop. Live acceptance of model changes remains pending; the historical same-model recovery observation does not establish fallback recovery.

Upgrade using the pause, uninstall, install, trust, and reload procedure in `0.3.0-preview` below. Existing goals do not restart automatically.

## 0.3.0-preview - 2026-11-14

- Check new business tool activity before Stop continuation. Allow one correction for a text-only manual turn, then request review if there is still no activity.
- Suppress prompts in plan mode and while visible tool or child activity is pending. Deduplicate callbacks and preserve the three-continuation limit across generations of the same host turn.
- Keep the complete objective in continuation prompts and the exception-recovery budget separate.
- Record the project installation's source version from `VERSION` instead of the fixed `0.1.0-preview` value; verify the saved installation record.
- Validated with all 77 offline tests, including installation-version checks, on 2026-11-14. Live runtime acceptance of the Stop changes remains pending; the `0.2.0-preview` recovery observation below does not establish it.

Upgrade: pause existing goals and native timers, uninstall the previous version, then install, verify trust, and reload this version. Existing goals do not restart automatically.

## 0.2.0-preview - 2026-11-07

Exception-recovery preview for the existing same-chat Goal-await workflow.

- Added opt-in `start --recover-errors`. New goals started through the Skill enable it by default; direct CLI use remains explicit.
- Added a default budget of two recovery attempts at two-minute intervals, configurable with `--recovery-max-attempts` from 1 through 5 and `--recovery-interval-minutes` from 1 through 60.
- Added bounded transcript evidence for the exact `max_output_tokens` terminal message and two exact premature-stream messages. Recovery does not infer failure from inactivity or resubmit arbitrary business work.
- Added same-chat, one-shot native `RDATE` recovery. Recovery and waiting share one automation ID; initial creation and later acknowledgement effects must be executed, and the initial actual ID must be bound from the native receipt.
- Added a recovery gate for the original chat, workspace, automation, prior and current turns, interruption state, duplicate handling, attempt count, delivery acknowledgement, and actual model. The automation payload does not override the model. A host may send one checker request to a fallback model, but the gate rejects business continuation on a mismatch.
- Added `recovery-enable` for an old RUNNING goal after a manual resume. Existing goals without recovery state keep their old behavior.
- Stop behavior for the complete objective is unchanged: continuation prompts retain the full objective, the per-turn Stop limit remains three, and exception recovery has a separate budget.
- Validated with 38 offline tests covering user and project installers, lifecycle behavior, recovery gates, duplicate handling, attempt counts, interruption and model guards. Inspected Windows desktop `26.1103.4239.0` with core `0.167.0-alpha.3.2`.
- Observed one real Kimi K3.1-Pro `max_output_tokens` interruption return through a native heartbeat to the same chat, admit recovery attempt 1 of 2 on the same model, and continue with an acknowledged business turn. This does not establish recovery for other failure classes, across restarts, or end-to-end business or scientific validity. Premature-stream recovery was validated offline only.

Upgrade note: pause existing goals and native timers, uninstall `0.1.0-preview`, then install and reload this version. Existing goals do not restart automatically.

## 0.1.0-preview - 2026-10-26

- First public preview for Windows Codex desktop.
- Added the Goal-await Skill, local state scripts, user and project installers, fixed Stop hook, same-chat native waiting, and lifecycle actions for starting, pausing, resuming, blocking, and completing goals.
- Added trust and reload guidance, installation conflict checks, configuration backups, and a standard-library-only runtime.
