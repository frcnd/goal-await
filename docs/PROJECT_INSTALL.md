# Goal-await 项目级安装

项目级安装只为指定项目启用 Goal-await：它会写入该项目自己的 Skill、Stop hook、忽略规则和运行状态。跨项目共用请使用 [README](../README.md) 中的用户级安装；AI 代用户执行安装或使用时，另读 [AI 安装与使用指南](AI_GUIDE.md)。

官方接口背景见 [Skill 路径](https://learn.chatgpt.com/docs/build-skills)和 [hooks 与信任](https://learn.chatgpt.com/docs/hooks)。

## 前置与环境

- 使用包含本项目[安装器](../install.py)和 `skill/goal-await/` 的源码目录。Skill 在安装时复制到目标项目，之后不依赖下载目录。
- 使用 Python 3.11+，准备一个已存在且路径准确的**目标项目目录**，并确认该项目可写。
- 使用已登录、能加载 Skill、原生 Stop 和 same-chat heartbeat 的本地 Codex 桌面环境。目前验证范围为 Windows；其他平台尚未验证。
- Windows 默认生成 PowerShell Stop 命令。实际宿主是 CMD 时，`plan` 与 `install` 都加 `--hook-shell cmd`；不要用另一种 shell 的离线结果推断可用。
- 项目的 `.gitignore` 和 `.codex/hooks.json` 若已存在，须为 UTF-8（可带 BOM）；hook 文件还须为有效 JSON。编码或结构不符时先保留原文件并核对。
- 一个项目只保留一种 Goal-await 安装。项目级和用户级 hook 会叠加，不要同时启用同名 Skill。

## 预览、安装与检查

在 Goal-await 源码目录中运行；把 `python` 换成实际 Python 3.11+ 可执行文件：

```powershell
python install.py plan --project "C:\work\my-project"
python install.py install --project "C:\work\my-project"
python install.py inspect --project "C:\work\my-project"
```

`plan` 展示目标路径、数据目录和将写入的准确 Stop 定义，不写文件；Python 路径可在命令中核对。核对计划后再执行 `install`。`inspect` 当前返回同样的计划信息，不能替代安装成功回执或实际 hook 检查。备份位置见下方说明。

目标项目已有 `.agents/skills/goal-await` 或尚未卸载的本安装记录时，安装器会拒绝覆盖。先保留副本和状态，核对来源，再使用对应安装器卸载；来源不明的副本不要直接删除。

需要切换安装范围时，先停止旧目标、完成卸载，再安装新范围。嵌套项目还应检查父目录中已有的 Skill 和 hook，避免重复启用。

## 准确信任 Stop

安装成功不等于 Stop 已获信任。在该项目的 Codex 会话中用 `/hooks` 查看来源为项目 `.codex/hooks.json` 的 Goal-await Stop，核对命令与 `plan` 输出的 `handler.command` 完全一致，只信任这一条定义。不要因 Skill 已加载而跳过信任，也不要批量信任其他 hook。

可选的[项目信任辅助](../tools/trust.py)仅适用于项目级安装。默认命令先只读显示准确 key、hash、状态和启用情况；确认后加 `--apply`：

```powershell
python tools/trust.py --project "C:\work\my-project" --codex "C:\path\to\codex.exe"
python tools/trust.py --project "C:\work\my-project" --codex "C:\path\to\codex.exe" --apply
```

该辅助通过 Codex App Server 的 hook 目录和配置接口工作，不启动模型或业务对话，不复制登录凭据，也不关闭全部 hook 的信任。它只写这一条定义的 key/hash，并在项目外备份用户 `config.toml`。它不适用于用户级安装；host 接口不同、项目未受信任或发现并发配置变化时应停止，改走宿主界面。

## 重载

信任后新建对话，或让原对话在空闲时正常重载配置。文件已写入、Skill 已显示或辅助进程已 reload，都不等于当前桌面会话已加载 Stop。不要归档或重启其他正在工作的对话。之后可在项目中输入 `$goal-await 开始：……`。

## 写入位置与备份

| 位置 | 内容 |
|---|---|
| `.agents/skills/goal-await/` | 自包含的 Skill、脚本和安装元数据 |
| `.codex/hooks.json` | 合并后的 Stop 定义，保留其他 hook |
| `.gitignore` | 仅在缺少时追加 `.goal-await/` 和 `.agents/skills/goal-await/` |
| `.goal-await/` | 当前对话绑定、目标状态、事件和安装记录 |
| `~/.codex/backups/goal-await/` | 默认的项目外时间戳备份；可用 `--backup-root` 改到项目外 |

安装器先预检并备份现有 hook、`.gitignore` 和已有安装记录，再写入；中途失败会回滚可识别的内容。备份根不能位于目标项目内。安装只复制本包文件，不写 Codex 登录、模型、provider 或业务产物。可选信任辅助另有自己的用户配置备份目录。

## 更新与卸载

更新或卸载前，完成或暂停所有相关目标，并通过原生界面确认定时任务已经停止。然后运行：

```powershell
python install.py uninstall --project "C:\work\my-project" --timers-paused
```

仅当确实没有待停止的原生任务时才省略 `--timers-paused`。卸载器会在仍有 `RUNNING`/`WAITING` 目标、Skill 被修改、或记录与准确 hook 不匹配时拒绝操作。成功时只删除未被改动的本安装 Skill 和这一条 Stop，保留状态、旧信任项与业务文件；旧信任项没有对应 hook 就不会执行。

更新时先卸载，再从新版本源码执行 `plan`、`install`，重新核对准确 Stop 并重载。未完成目标不会自动重启。若 `.gitignore` 在安装后被他人改过，不要用整份旧备份覆盖它，只核对并移除本安装追加的两行。

当前支持范围和使用限制见 [README](../README.md)。
