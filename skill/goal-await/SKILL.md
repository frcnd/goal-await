---
name: goal-await
description: 用 Goal-await 在当前桌面对话持续推进明确目标，安排定时等待，暂停、恢复或完成目标。适用于用户要求 Goal-await 或持续目标执行。
---

# Goal-await

在当前对话推进完整目标；需要等待时用原生定时跟进，完整验收后完成。默认检查回到当前对话并保持其模型，不新建检查对话、不切换模型、不启动原生 Goal。

必须先按项目级或用户级安装 Skill 和固定 Stop hook，并逐定义信任。单独复制 SKILL.md 不会自动启用 hook。安装后新建或重新加载对话；不要在仍工作的对话自行归档、重启。已安装后，每个对话同时绑定一个未完成目标，DONE 后可开始下一个，不用逐目标改 hook/信任。用户级只扩大安装的适用范围，不扩大任何业务授权；不在未调用本 Skill 的对话自动创建目标。

## 安装后命令位置

下面 JSON 中的路径由安装器填写；从字段读取路径，按宿主 shell 正确引用，不要把 JSON 转义当成路径字符。

```json
{"python": {{PYTHON_JSON}}, "entry": {{ENTRY_JSON}}, "scope": {{SCOPE_JSON}}}
```

在当前对话使用 shell 工具执行 `python -X utf8 entry 动作 参数`，替换为上面的实际路径。保留当前 `CODEX_THREAD_ID`，不要伪造对话 ID。执行目录、start 的 workspace 都与当前桌面对话实际工作目录一致。

`status` 返回 `active: false` 只表示当前对话没有绑定目标，不表示安装失败。用户已要求用本 Skill 推进明确目标时，由模型执行 `start` 完成绑定，无需用户手工写状态。它与原生 Goal 是两套独立状态；不要因此创建原生 Goal 或另建定时任务替代本流程。

判断命令结果时同时检查退出码、stdout 中的 JSON 和 stderr，不把单条警告当作失败。Windows Python 可能输出 `Failed to find real location of ...`：这表示启动时查询最终路径失败，并不证明可执行文件不存在。若退出码为 0 且返回有效的预期 JSON，按返回状态继续；若非零、缺少预期结果或 JSON 含错误，再调查路径、权限与具体错误。不要屏蔽全部 stderr、自动更换 Python 或关闭沙箱。用户级状态写入被拒绝时，使用宿主正常审批流程。

用户级状态在安装记录指定的用户数据目录，可能需要宿主批准该目录的读写；不得自行关闭沙箱或扩大其他目录权限。若当前项目仍有同名项目级安装，先保留它的状态并处理安装范围冲突，不双重启动。旧目标已完成且宿主已删除其 heartbeat 时，不对旧 ID 更新或重建任务；只有新目标第一次等待才创建新任务。

用户可以用中文、英文或其模型理解的语言，例如 `$goal-await 开始……`、`$goal-await wait……`。不需要手动逐步输入等待和完成；完整目标已明确时按进度自行处理。以下是脚本动作，不是新增的内置 `/goal`。

| 用户意图 | 脚本动作与输入 |
|---|---|
| 开始 | `start --objective '完整目标及验收标准' --workspace '当前对话目录' --context-summary '进展、下一步、证据入口'` |
| 等待 | `wait --waiting-for '在等什么' --completion '怎样算完成' --result-location '结果/日志/查询位置'`；按任务安排 interval-minutes、timeout-minutes、max-checks，可加 guide |
| 暂停 | `pause --reason '用户原因'` |
| 恢复 | `resume`，恢复当前目标或原等待，不重新提交业务作业 |
| 完成 | 独立核对完整目标后 `complete --reason '验收依据'` |
| 需用户处理 | `blocked --reason '缺少的必要输入、授权或具体边界'` |
| 查看/摘要 | `status`；`context-update --context-summary '最新进展' --decision '最近决定'` |

如果已有未完成目标，继续或恢复它，不用新目标绕过暂停。开始后立即做已授权工作，Stop 会在 RUNNING 且模型准备停止时提醒继续；等待、暂停、blocked、完成允许结束本轮。单轮接续和观察预算耗尽会进入 NEEDS_REVIEW，应说明原因。用户中断后保持停止。

## 原生定时接线

读取动作返回的完整 JSON；effects 表示待执行操作，不代表已安排或已暂停。

1. 加载宿主的 `automation_update`，按 `effects.automation` 原字段执行。保留 prompt、等待版本、rrule、目标对话、kind、status、mode 和已有 id，不改模型。没有效果就不调用。
2. 首次创建必须直接调用原生工具、读取实际 ID，再用 `bind --automation-id '实际ID'` 保存。回执不明确先查看准确任务，不重复创建。以后 wait、pause、resume、blocked、complete 复用此 ID。
3. 原生工具失败时说明失败，保留状态，不声称操作成功，不启动替代私有计时服务或扩大权限。返回 accepted=false 但含暂停效果时，仍处理这项暂停。

原生 heartbeat 执行短 check 入口。读取本轮准入返回的 context/instructions/observation_id，自主只读调查已有文件、日志、进程或获授权的接口；检查脚本只是可选证据。工具错误不证明业务失败或 blocked，无实质变化保持安静，完成、真实失败或需用户处理才通知。

report 严格绑定本轮编号和等待版本。就绪/真实失败后先执行返回的定时暂停；有 local_continuation 时，在本对话执行其 acknowledge_command，再继续完整目标。不要向自己发送消息，不调用 claim/sent。业务失败交回本对话分析，不自动重跑，不把它当完整目标完成。

现有宿主如果提供 functions.exec，可在已有定时绑定后使用返回的 execute_effects 透传更新；否则按结构化 effects 调用原生工具。初次创建不要使用该助手，它不会 bind 新 ID。编排代码不是 shell 命令。
