# Goal-await

[English](README.md) | **简体中文**

让 Codex 在当前桌面对话中持续推进目标：需要等待时安排定时检查，条件满足后接着做，验收完成后结束。支持开始、等待、暂停、恢复和完成。

Goal-await 由 **Skill、本地状态脚本和固定 Stop hook** 组成，使用 Codex 原生定时任务回到同一对话。安装后，新目标不需要重新配置 hook；运行和安装只依赖 Python 标准库。

当前公开预览版为 `0.3.1-preview`，为 Codex 设计。目前验证范围为 Windows 桌面；其他平台、其他失败类别、跨重启恢复和长期无人值守尚未验证。版本历史见[版本记录](CHANGELOG.zh-CN.md)。

## 使用

安装并重新加载后，在项目对话中输入完整目标，例如：

```text
$goal-await 运行实验，等待结果并核对，最后生成报告。
$goal-await 暂停
$goal-await 恢复
$goal-await 完成
```

通常只需给出一次目标，AI 会按进度处理等待和完成。请说明期望产物、验收标准，以及允许使用的资源；需要等待时，提供结果或日志的位置。已有上下文中的信息不用重复。

每个对话同时管理一个未完成目标。模型选择沿用宿主及用户配置；手动切换或宿主回退不会重新绑定目标，也不会补回已消耗的恢复次数。等待次数、时间或接续预算耗尽后会停止自动推进并说明原因。`$goal-await` 是 Skill 调用，与原生 Goal 分别管理状态，不是内置 `/goal` 命令。

Stop 自动接续前会检查本轮是否产生了新的业务工具活动。只有文字的手工轮次可得到一次纠正性接续；之后若仍无新活动，就停止自动接续并转入复查。计划模式或存在可见等待时不追加催促。继续提示仍携带完整目标，每个宿主轮次最多三次接续的上限保持不变。

Skill 开始新目标时会默认加入 `--recover-errors`；直接使用 CLI 时需要显式启用。恢复默认最多两次、间隔两分钟，可用 `--recovery-max-attempts`（1 至 5）和 `--recovery-interval-minutes`（1 至 60）调整。

## 异常中断恢复

只有当前对话记录中出现下列完整终止错误之一时，恢复才会处理一个已结束的助手轮次；匹配不区分大小写：

- `Incomplete response returned, reason: max_output_tokens`
- `stream disconnected before completion`
- `response stream disconnected before completion`

恢复不会根据长时间无更新推断失败，也不会任意重新提交业务工作。继续之前，Skill 会核对原对话、工作目录、定时任务、前一业务轮次、中断状态、恢复次数，以及上一次接续是否已确认。它从已落盘产物和最新上下文接着做，先核对副作用，再执行尚未完成的步骤。每个失败轮次最多只能获得一次恢复准入。

恢复使用原生一次性 `RDATE` 任务，并与后续等待复用同一个定时任务 ID。首次执行返回的 effects 后，从原生工具回执读取实际 ID 并 bind；之后的更新沿用该 ID。acknowledge 返回定时 effects 时也须执行。JSON 中的 effects 只有宿主操作成功后才算完成。自动化 payload 仍指向原对话，但不覆盖模型选择。

模型选择仍由宿主及用户配置决定。手动切换或宿主回退后，等待检查、接续确认和有限次异常恢复都可继续，无需重新绑定目标，也不会重置剩余恢复预算。开始时记录的模型仅作诊断信息。用户中断、未知错误、不支持或格式异常的记录、恢复次数用完或接续票据未确认时，自动恢复都会停止。若所选模型在首次恢复准入或下一次单次唤醒重新预约之前失败，该次调度已经消耗，需要用户手动继续。

Stop 接续现已使用上述活动检查。继续提示仍携带完整目标，每宿主轮次仍最多三次 Stop 接续；异常恢复另有默认两次预算，不占用 Stop 次数。

缺少恢复字段的旧目标保持原有行为。从较早版本升级时，先暂停目标与原生定时任务，卸载当前安装，再安装 `0.3.1-preview`，核对信任并重载。未完成目标不会自动重启。旧 RUNNING 目标经人工恢复后，可在同一业务对话调用 `recovery-enable` 并执行返回的 effects，为后续异常增加恢复保护。

`0.2.0-preview` 的恢复功能通过 38 项离线测试验证用户级与项目级安装器、生命周期、恢复准入、去重、次数、用户中断和模型保护。检查了 Windows 桌面 `26.1103.4239.0`、core `0.167.0-alpha.3.2` 的调度实现。该版曾观察到一次 Kimi K3.1-Pro 真实 `max_output_tokens` 中断经原生 heartbeat 回到同一对话，以同一模型获准恢复第 1/2 次，并观察到业务接续得到确认。该结果不能证明其他失败类别、跨重启或端到端业务与科学结论的有效性；响应流提前断开的恢复仅作离线验证。Stop 活动检查与宿主模型选择行为仅通过离线验证，真实运行验收仍待完成，也不扩展上述恢复实测结论。

## 主动使用或手动开启

默认情况下，AI 可以在认为任务适合时主动选择 Goal-await。安装本身不会开始目标；选择并执行 Skill 的开始动作后，当前对话才会绑定目标。

如果你希望每次都由自己明确开启，可以在**安装前**编辑仓库中的 [skill/goal-await/agents/openai.yaml](skill/goal-await/agents/openai.yaml)，保留已有 `interface`，在同一级添加：

```yaml
policy:
  allow_implicit_invocation: false
```

然后按下方流程安装，用 `$goal-await` 显式调用。这是 Codex 的[调用策略设置](https://learn.chatgpt.com/docs/build-skills#optional-metadata)：关闭根据任务自动调用，仍允许显式调用；它不限制 AI 读取文件的权限。

已有安装请先按本文的更新步骤卸载，再修改仓库源文件并重新安装。不要直接改已安装的副本：安装器会校验文件，修改过的副本需要保留并核对后才能卸载。该设置不负责暂停已经开始的目标。

## 安装

需要 Python 3.11+，以及已登录、可使用本地 Python、Stop hook 和原生同对话 heartbeat 定时任务的 Codex 桌面环境。宿主需要提供真实 `CODEX_THREAD_ID` 和 `automation_update` 接口。

| 安装范围 | 适合的用途 | 入口 |
|---|---|---|
| 当前用户 | 多个项目共用一份安装 | 下方步骤 |
| 单个项目 | 只在选定项目中使用 | [项目安装指南](docs/PROJECT_INSTALL.zh-CN.md) |

两种范围选一种，避免在同一项目同时启用同名安装。单独复制 Skill 文件不能完成本包的路径生成和 hook 登记，请使用本仓库安装器。

### 当前用户安装

下载或克隆仓库后，在仓库目录运行。先查看预览，核对路径和命令，再安装：

```powershell
python tools/install_user.py plan
python tools/install_user.py install
```

Windows 默认使用 PowerShell 生成 hook 命令；若 Codex 对话实际使用 CMD，两条命令都加 `--hook-shell cmd`。`python` 应指向可用的 Python 3.11+；必要时使用其完整路径。

默认写入位置如下；`CODEX_HOME` 未设置时为 `~/.codex`。

| 内容 | 位置 |
|---|---|
| Skill 和脚本 | `~/.agents/skills/goal-await/` |
| 固定 Stop hook | `$CODEX_HOME/hooks.json` |
| 目标状态和安装记录 | `$CODEX_HOME/goal-await/` |
| 配置备份 | `$CODEX_HOME/backups/goal-await-user/` |

预览不写文件。安装会备份已有配置并合并自己的 hook，保留其他 hook；不修改登录、模型或 provider。安装副本自包含，之后不依赖仓库所在位置。

**安装后还需要信任和重载：**

1. 在 Codex hooks 界面或 CLI `/hooks` 中找到用户配置来源的 Goal-await Stop，核对命令与预览一致，只信任这一条。
2. 新建对话，或在原对话空闲时正常重新加载配置，然后使用 `$goal-await`。

文件写入成功不代表已打开的对话已加载。项目用的 `tools/trust.py --project` 不适用于用户级安装。若运行时无法写入状态目录，按宿主的权限流程只授权所需目录。

可随时查看安装记录：

```powershell
python tools/install_user.py inspect
```

遇到已有同名 Skill 时，安装器会停止。先核对来源；本包安装的副本使用对应安装器卸载，其他来源的副本先备份再移出安装位置。

### 更新或卸载

先暂停或完成所有使用这份安装的目标，并在原生任务界面确认相关定时任务已暂停或删除，再执行：

```powershell
python tools/install_user.py uninstall --timers-paused
```

卸载移除本包未被修改的安装副本和自己的 hook，保留目标记录、业务产物及其他 hook。文件已被修改时会停止，需先保留并核对改动。旧信任项可能保留，可按准确的单条定义清理；不要用整份旧备份覆盖较新的配置。

更新时，卸载后用新版本重新预览、安装、核对信任并重载。未完成目标不会自动重启。项目级卸载命令见[项目安装指南](docs/PROJECT_INSTALL.zh-CN.md)。

## 让 AI 帮你安装

可以把 [AI 安装与使用指南](docs/AI_GUIDE.zh-CN.md) 交给 Codex，并告诉它要安装到当前用户还是哪个项目。该指南用于安装和使用协助；安装后的运行流程由 Skill 提供。

## 反馈与贡献

问题反馈和开发检查见 [CONTRIBUTING.md](CONTRIBUTING.zh-CN.md)；版本历史见 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)。请只提供必要的脱敏信息。

本项目采用 [MIT 许可证](LICENSE)。允许使用、修改、商用和再分发，须保留版权及许可声明。
