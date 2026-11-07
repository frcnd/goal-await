# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

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
