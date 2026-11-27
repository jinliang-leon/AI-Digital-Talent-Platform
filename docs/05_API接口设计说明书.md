# API 接口设计说明书

> 当前实现以运行中的 OpenAPI 为准。简历批次先调用 `POST /resume-batches`，再通过 `POST /resume-batches/{id}/files?filename=...` 单独提交二进制，每份独立记录状态。候选人已有原件可调用 `POST /files/{id}/parse`。审核支持 `GET/PATCH /resume-uploads/{id}/draft` 和 `POST /resume-uploads/{id}/confirm`。知识库支持正文提交或二进制文件上传，文档更新生成新版本。岗位证据分析使用 `POST /applications/{id}/analyses`，面试提纲建立在已人工审核的分析上。招聘助手 SSE 当前发送 `meta`、心跳、`result` 和 `done`，失败发送 `error`，历史接口可恢复结果。

> 第一阶段运行态契约见 /docs。实现登录、注销、登录态、用户与授权、工作台、岗位、候选人、应聘指派与流转、重开、反馈、状态历史、单件上传下载及审计。上传使用 POST /candidates/{id}/files?filename=... 和 application/octet-stream。文件读取走 GET /files/{id}/download。列表为 offset/limit，岗位和候选人编辑以及流程变更使用 expected_version。刷新令牌、批量上传和其余 AI API 为后续目标。

v0.1 · 待评审

统一前缀 /api/v1。Bearer JWT 从登录态取得主体，禁止客户端以 user_id 覆盖。响应通过 HTTP 状态码区分结果，失败体包含 code、message、trace_id、field_errors。分页默认 20 条，上限 100。时间为带时区 ISO 8601。业务写操作采用结构化请求并写审计。可重放的创建接口接受 Idempotency-Key，状态流转接受 expected_version。

| 功能 | 方法与路由 | 关键输入及输出 |
| --- | --- | --- |
| 身份和授权 | POST /auth/login、/auth/refresh、/auth/logout；GET /auth/me | 登录、轮换刷新令牌、注销、有效权限 |
| 管理 | GET/POST /users、/roles；PATCH /users/{id}、/roles/{id}；GET /audit-logs | 授权范围内用户、角色、权限和审计 |
| 工作台 | GET /dashboard | 指标、待办、按岗位进度与最近动态 |
| 岗位 | GET/POST /jobs；GET/PATCH /jobs/{id}；POST /jobs/{id}/close | 状态查询、字段更新、关闭原因及版本 |
| JD | POST /jobs/{id}/requirement-drafts；POST /jobs/{id}/requirements/confirm | 自然语言需求、生成版本、人工确认要求及修订号 |
| 上传与任务 | POST /resume-batches；GET /resume-batches/{id}；GET /tasks/{id}；POST /tasks/{id}/retry | multipart，202 返回批次和任务；任务状态、可重试错误 |
| 简历审核 | GET/PATCH /resume-uploads/{id}/draft；POST /resume-uploads/{id}/confirm | 字段、证据锚点、修订号及候选人关联选择 |
| 候选人 | GET/POST /candidates；GET/PATCH/DELETE /candidates/{id}；POST /candidates/{id}/notes | 分页筛选、详情、备注、删除任务 |
| 导入导出 | POST /candidate-imports、/candidate-exports；GET /files/{id}/download | 数据来源声明、范围和字段选项、任务及权限校验 |
| 搜索 | POST /talent-search | query、显式 filters、可选会话引用；返回应用条件、候选人证据及来源 |
| 应聘 | POST /applications；GET /applications/{id}；GET /applications/{id}/status-history；POST /applications/{id}/transitions | 候选人、岗位、版本、目标状态和原因 |
| 岗位证据 | POST /applications/{id}/analyses；GET /analyses/{id}；POST /analyses/{id}/review | 按要求的证据与追问建议，引用固定输入修订 |
| 面试 | POST /applications/{id}/interviews；GET/PATCH /interviews/{id}；POST /interviews/{id}/questions/generate；PATCH /interviews/{id}/questions/{qid}；PUT /interviews/{id}/feedback | 指派、题目配置与编辑、人工反馈和可选摘要 |
| 知识库 | GET/POST /knowledge-bases；GET/POST /knowledge-bases/{id}/documents；PUT/DELETE /knowledge-documents/{id}；POST /knowledge-bases/{id}/search | 版本、处理任务、权限及引用 |
| ChatAI | GET/POST /chat/sessions；GET /chat/sessions/{id}/messages；POST /chat/sessions/{id}/stream | 持久化消息，返回 SSE 与引用 |

上传限制初始建议单文件 20 MiB、每批 50 份，同时约束页数、像素和解包体积。按文件返回失败或重复提示。索引任务对业务状态只提供进度信息。越权对象按入口策略返回 403 或防枚举 404。常用错误为 409 VERSION_CONFLICT、413 FILE_TOO_LARGE、415 UNSUPPORTED_MEDIA_TYPE、422 INVALID_INPUT、429 RATE_LIMITED。

## SSE 契约

同源 fetch 用 POST、Bearer 和 ReadableStream。响应中的 meta 携带 trace_id、message_id；随后可以发 delta、citations、usage、done 或 error。流建立前可返回普通 HTTP 错误，建立后以 error 事件结束。先做权限、输入和范围校验再开始推送。断线后前端从消息接口恢复已持久化的结果或显示中断，重新发送使用稳定的客户端消息 ID。涉及候选人数据的引用走授权详情端点。
