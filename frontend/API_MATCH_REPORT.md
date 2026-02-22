# 前端与 joyhousebot API 匹配检查报告

## 1. 路由与方法对照

| 前端调用 | 后端路由 | 方法 | 状态 |
|----------|----------|------|------|
| getAgent | /agent | GET | 一致 |
| getAgents | /agents | GET | 一致 |
| patchAgent | /agents/{agent_id} | PATCH | 一致 |
| getSessions | /sessions?agent_id= | GET | 一致 |
| getSessionHistory | /sessions/{key}/history?agent_id= | GET | 一致 |
| deleteSession | /sessions/{key}?agent_id= | DELETE | 一致 |
| sendMessage | /chat | POST | 一致 |
| sendMessageStream | /v1/chat/completions | POST | 一致 |
| getConfig | /config | GET | 一致 |
| updateConfig | /config | PUT | 一致 |
| listCronJobs | /cron/jobs?include_disabled= | GET | 一致 |
| addCronJob | /cron/jobs | POST | 一致 |
| patchCronJob | /cron/jobs/{job_id} | PATCH | 一致 |
| deleteCronJob | /cron/jobs/{job_id} | DELETE | 一致 |
| runCronJob | /cron/jobs/{job_id}/run?force= | POST | 一致 |
| getOverview | /control/overview | GET | 一致 |
| getChannels | /control/channels | GET | 一致 |
| getPresence | /control/presence | GET | 一致 |
| getSkills | /skills | GET | 一致 |
| patchSkill | /skills/{name} | PATCH | 一致 |

## 2. 请求/响应结构

- POST /chat: 前端传 message, session_id；后端支持可选 agent_id。一致。
- POST /v1/chat/completions: 前端传 model, messages, stream, session_id, agent_id；与后端 OpenAIChatCompletionsRequest 一致。
- GET /config 返回 ok, data（含 agents.defaults, providers, channels, tools, gateway, wallet 等）；与前端 ConfigData 一致。tools 中含 exec（含 container_*）后端已支持。
- PUT /config: 前端传 agents, providers, channels, tools, gateway, wallet；后端 ConfigUpdate 兼容并支持 skills/plugins 可选。
- Agent/Session/Cron/Control/Skills 的请求体与响应体与后端实现一致。

## 3. 后端有、前端未用的接口

GET /, /health, POST /message/send, /control/auth-profiles, /control/actions-catalog, /control/actions/validate*, /control/alerts-lifecycle, /tasks, /identity, POST /transcribe, WebSocket /ws/chat。属扩展能力，不影响现有匹配。

## 4. 结论

前端使用的 20 个 HTTP 接口与 joyhousebot 当前 API 实现一一对应，请求/响应结构与后端一致，匹配。
