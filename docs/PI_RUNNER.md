# Pi Runner Pilot

`extensions/capability-pi-runner` 把 Pi 作为普通、可替换的长程 Invocation Capability：
`coding.pi.execute@1.0.0`。它不是 Runtime 的第二套 Agent/Task 状态机。

Pilot 的边界：

- 输入只接受安装器登记的 `workspace_ref`、精确 Git revision、指令和 allowlist test profile；
- 每次执行创建 detached 临时 worktree；
- Pi 通过 RPC JSONL 和 Host Model Gateway 调用模型；
- 只开放 `read/edit/write/grep/find/ls`，不开放 Pi `bash`；
- 测试命令由安装时的 profile 固定，使用 argv 启动且不经过 Shell；
- 输出是待审阅 patch、摘要和测试证据，`applied=false`；
- 不 commit、merge、deploy，也不写外部系统；
- 重启后无法证明结果的 operation 进入 `manual_required`，不伪造成功。

Pi package、npm integrity、Node runtime 与入口均精确锁定。发行包内置 Node `v24.19.0` LTS，不依赖用户
全局 Node。未来为 Pi 增加动态工具时，只能使用 Host Tool Broker 的冻结 allowlist 和短期 `jht_` grant。
