# Pi Runner Capability

`coding.pi.execute@1.0.0` 将 Pi 作为可替换的 Node 执行器接入 joyhousebot，而不是创建第二套 Run/Task
状态机。Runtime 冻结用户、Run、Action、workspace ref、精确 Git revision、模型版本和预算；Device Host
领取短期 model grant 后，以签名的临时上下文交给本地 Supervisor。

首期安全边界：

- workspace 只能来自 `PI_WORKSPACES_JSON` 的本机 allowlist，并创建 detached 临时 worktree；
- revision 必须是精确十六进制 Git revision；
- Pi 禁用 Extension、Skill、Prompt Template、Theme、项目 Context 和 Bash；
- 只开放 `read/edit/write/grep/find/ls`，测试命令由 Host 配置 allowlist 单独执行；
- Provider key 永不进入 Pi；`models.json` 只保存 `$JOYHOUSEBOT_MODEL_GRANT` 引用；
- 输出仅为未应用的 patch、测试证据和有界摘要，不 commit、merge、deploy 或外部写入；
- Worker 重启后，遗失的 Pi 进程进入 `manual_required`，保留 worktree 供人工检查，不伪造恢复成功。

生产 Extension 配置必须固定 `PI_ENTRYPOINT`、`PI_IMPLEMENTATION_DIGEST`、`PI_STATE_PATH`、
`PI_WORKSPACE_ROOT` 和 `PI_WORKSPACES_JSON`。Desktop/Server 使用 `hosts/node/runtime-lock.json` 中的
Node v24.19.0，并按本目录 `runtime-lock.json` 固定 Pi 0.84.2；运行期不得执行 `npm install`。
