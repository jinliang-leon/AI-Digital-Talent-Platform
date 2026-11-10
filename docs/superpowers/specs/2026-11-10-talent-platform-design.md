# AI数字化人才平台首版设计

日期：2026-11-10（Asia/Shanghai）
状态：待审阅；本文件不代表已批准开始实现。
需求来源：[需求基线](../../requirements.md)。

## 1. 目标、范围与完成条件

为企业 HR 提供课程／作品演示用招聘工作台：上传简历后形成可核查档案，再通过岗位匹配和自然语言检索找到人才。首版使用 30 份虚构简历、5 个岗位 JD。

交付：可操作中文网页、FastAPI API、异步简历处理、人才画像、标签、JD 管理、双向匹配、带来源 RAG 检索、JWT/RBAC、测试与 Docker 部署说明。

不包含：扫描件 OCR、旧版二进制 DOC、智能测评、多企业租户、候选人自助注册、自动录用／淘汰、生产级高可用。
这些首版边界中，OCR／测评延期已经用户确认；其余为面向课程演示的工程建议。

所有模型输出都需可追溯；原文未出现的信息以“未提供”处理，不将生成内容伪装成简历事实。匹配用于 HR 辅助判断。

## 2. 已确认事项与工程选择

用户确认：指定完整技术栈、网页交付、文本 PDF/DOCX、两种角色、演示样本规模、双向匹配和原文证据、对话网关与本地 Embedding。

默认对话模型 Spark-X2.6 由用户明确指定。下文的模块边界、模板网页、数据表、匹配权重、错误策略和验收阈值是工程选择，随本设计一起审阅。

### 2.1 考虑的架构方案

| 方案 | 优点 | 成本与适用性 |
| --- | --- | --- |
| 模块化单体 + 独立 worker（采用建议） | 一套 Python 代码；API 与耗时任务分进程；可清楚展示所有组件职责 | 需要任务状态与跨存储同步；适合本次规模 |
| 多个业务微服务 | 业务隔离、可独立扩展 | 多套部署与鉴权、链路调试更重，本次规模收益有限 |

不采用在 HTTP 请求内完成全部解析的方式：模型慢请求和文件处理会直接阻塞上传体验，失败恢复也更难。

### 2.2 技术细化建议

- Python 3.11 容器运行时，依赖在实现阶段验证兼容性并锁定；不修改系统默认 Python。
- FastAPI + Pydantic；SQLAlchemy 2 + PyMySQL + Alembic 使用本地 MySQL。
- MVC：Model 为业务实体／持久化；Controller 为路由与权限；View 为响应 Schema、Jinja2 模板及静态资源。Service 承载业务，Repository 隔离存储，避免把业务塞进路由。
- Jinja2 + HTMX + CSS 提供中文网页，不额外引入独立前端构建系统。
- LangChain 管理模型、Prompt 与检索适配；LangGraph 编排解析、画像、入库与检索流程。
- Redis + RQ 处理后台任务；任务以 MySQL 状态为恢复依据，不只依赖队列。
- 密码使用 Argon2；JWT 使用成熟库，算法固定、签名密钥外部配置。

## 3. 运行拓扑与组件职责

浏览器 → FastAPI Web/API → Service/Repository → MySQL、Redis、MinIO、Milvus、Neo4j。
RQ worker 消费 Redis 任务，调用 LangGraph、对话网关与 Ollama，更新各存储。
浏览器只访问应用，不持有模型密钥，不直连数据库。

| 组件 | 唯一主要职责 |
| --- | --- |
| 本地 MySQL | 账号权限、人才／JD 主数据、文件版本、任务、证据、匹配记录、同步状态；事实来源 |
| MinIO | 简历原文件；业务桶与 Milvus 内部对象存储严格区分 |
| Milvus | BGE-M3 稠密向量与检索标量；不是人才主数据库 |
| Neo4j | 人才／岗位与规范化技能关系，支撑技能交集与缺口解释 |
| Redis | 任务队列、短期检索缓存、限流／令牌失效状态 |
| 对话网关 | 结构化提取、画像摘要、基于证据回答；不负责最终权限与硬条件判断 |
| Ollama | 本地 BGE-M3 文本向量化 |

首版应用与 worker 使用同一个镜像。默认复用本机已有基础服务，使用项目专用数据库、桶、集合及图命名空间。保留可选的独立依赖 Compose 配置，但不能覆盖或停止用户既有容器。

## 4. 网页与主要操作

导航：人才库｜岗位管理｜智能检索｜处理任务；管理员额外有账号管理。

人才库 → 上传 PDF/DOCX → 任务进度 → 档案详情（摘要、技能、经历、原文来源）→ 编辑／重建索引。
岗位管理 → 新建 JD → 查看候选人排序 → 打开匹配解释与证据。
人才详情 → 推荐岗位，使用同一匹配规则。
智能检索 → 输入问题 → 人才结果与带证据回答 → 打开引用原文／原文件。

错误必须可见：失败阶段、可理解的原因、重试入口。尚未索引完成的档案显示“处理中”，不混入正常匹配结果。

## 5. 权限、鉴权与账号

| 能力 | HR | 管理员 |
| --- | --- | --- |
| 登录、退出、查看自身信息 | 是 | 是 |
| 简历上传、档案编辑、标签、JD、匹配、检索 | 是 | 可同时分配 HR 角色 |
| 账号启停、分配角色、查看角色 | 否 | 是 |

首版为单组织共享人才池；多企业隔离不在范围内。业务权限在 API 服务端检查，不能只隐藏网页按钮。

- 无公开注册；首个管理员通过本地初始化命令建立，不在源码写默认密码。
- 访问 JWT 有效期建议 60 分钟，含 sub、jti、exp、权限版本；每次请求检查账号启用状态与权限版本。
- 首版无自动刷新，过期重新登录；退出将 jti 加入 Redis 黑名单直至过期。
- 浏览器用 HttpOnly、SameSite Cookie 保存 JWT；生产 HTTPS 开启 Secure；修改请求校验 CSRF。API 客户端可使用 Bearer JWT。
- Redis 鉴权检查不可用时返回服务不可用，不静默跳过失效检查。

## 6. 数据库设计

所有业务 ID 由应用产生 UUID；SQL 使用 utf8mb4，时间使用 UTC。主数据保存 revision、created_at、updated_at；需要审计的操作保存操作者。

| 表 | 关键字段与约束 |
| --- | --- |
| users | id, username UNIQUE, password_hash, is_active, auth_version |
| roles | id, code UNIQUE（admin/hr） |
| permissions | id, code UNIQUE |
| user_roles / role_permissions | 联合唯一键与外键 |
| talents | id, name, summary, skills_json, experience_json, education_json, revision, active_document_id, index_status, deleted_at |
| resume_documents | id, talent_id, original_name, object_key UNIQUE, sha256, mime_type, size_bytes, revision, status, extracted_text |
| source_chunks | id, document_id, document_revision, chunk_no, text, page_no, paragraph_no, char_start, char_end；文档版本+序号唯一 |
| extraction_evidence | id, talent_id, document_id, document_revision, field_path, chunk_id, quote, verification_status |
| tags / talent_tags | 标签名称唯一；人才与标签联合唯一 |
| jobs | id, title, raw_jd, requirements_json, revision, status, created_by |
| processing_tasks | id, task_type, entity_id, revision, state, stage, attempts, error_code, safe_error_message, started_at, finished_at |
| index_outbox | id, entity_type, entity_id, revision, operation, payload_json, state, attempts, next_attempt_at；幂等业务键唯一 |
| match_results | id, job_id, job_revision, talent_id, talent_revision, algorithm_version, embedding_model, score_json, evidence_json, created_at；完整版本组合唯一 |
| audit_logs | id, actor_id, action, entity_type, entity_id, outcome, created_at；不保存密码或模型密钥 |

- 人名不用于去重；原文件 sha256 用于幂等上传和重复提醒。更新同一人才需由 HR 明确指定，不自动合并同名人才。
- 任务查询索引：(state, updated_at)；同步重试索引：(state, next_attempt_at)。
- SQL 外键、唯一约束负责完整性；上传大小与业务约束由服务层验证。
- 原文件删除采用软删除工作流；权限校验先于签名下载链接生成。

### 6.1 Milvus

建议集合：talent_platform_chunks_bgem3_v1。
字段：chunk_id 主键、talent_id、document_id、document_revision、embedding_model、embedding_version、vector FLOAT_VECTOR(1024)。
使用 COSINE，索引参数根据现有 Milvus 版本验证；首版 30 份简历不追求大规模参数调优。
更换模型／维度使用新集合重建，不混用不同向量空间。用户问题与简历切片必须用同一 Embedding 配置。

JD 向量另设 job 向量集合，带 job_id、job_revision 和同样的模型版本字段，用于反向找岗位。
检索结果必须回查 SQL 的当前 revision、删除状态和就绪状态，过滤陈旧向量。

### 6.2 Neo4j

节点：Talent(project_id, talent_id)、Job(project_id, job_id)、Skill(project_id, normalized_name)。
关系：Talent-HAS_SKILL→Skill；Job-REQUIRES_SKILL→Skill；保存来源版本及 chunk_id，使用参数化 Cypher。
唯一约束覆盖 project_id 与业务 ID；所有查询限定项目命名空间，避免影响现有图数据。
没有图数据时不能编造技能关系；严格匹配端点明确提示索引尚未就绪。

## 7. 简历处理流程与一致性

上传：检查 JWT/RBAC → 扩展名、文件签名与大小（建议 10 MiB）→ 生成随机对象键上传 MinIO → MySQL 写文件、任务及 outbox → 返回 202 与 task_id。

LangGraph 处理节点：
读取文件 → 提取带页／段定位的文本 → 结构化提取 → Schema 校验与证据核验 → 画像摘要 → 切片 → 本地向量化 → 同步 Milvus／Neo4j → 标记 READY。

- PDF 保留页码；DOCX 按段落定位，不伪造固定页码。
- 纯扫描 PDF／无可用文本返回明确的 OCR_NOT_SUPPORTED，不输出空档案或臆测内容。
- 简历内容视为待分析数据，不能执行文档内指令；模型无工具执行权限。
- 结构化失败可用一次受控修复；证据摘录必须能定位至原文，不成立则该字段为空或需 HR 复核。
- MySQL 为事实源；不存在跨 MySQL、Milvus、Neo4j 的单个事务。
- 使用 MySQL outbox + 幂等 upsert；Redis 入队失败时调度器可从 outbox 恢复。
- 状态：QUEUED → RUNNING → READY／FAILED；索引可细分 PENDING／PARTIAL／READY／FAILED。
- 只有当前版本所有必要索引就绪才进入正常匹配；失联 worker 用心跳与超时回收。
- 用户改档案／删除／换简历后递增 revision，同步重建；缓存失效，旧版本向量即使尚未物理删除也不能被展示。
- 对象上传与 SQL 写入之间失败产生的孤儿对象由补偿任务清理。
- 后台调用限制并发，建议初始 worker 并发 1，避免占满本地内存和模型资源。

## 8. 双向匹配与 RAG

### 8.1 双向匹配

先过滤启用、未删除、已就绪数据；只采用 HR 明确设置的岗位相关硬条件。
缺失简历信息不推断满足硬条件，显示“未知／需确认”。

候选召回：Milvus 语义相似度；结合图中的技能交集和 MySQL 的经历／项目证据。
为了让反向“人才找岗位”一致，最终统一执行 match(job, talent)，不反转两套独立评分。

工程建议权重：技能覆盖 50%、经历／项目要求覆盖 30%、语义相关 20%。
- 经历覆盖按岗位列出的可核查要求逐项计算；条件未指定或未知时标明状态，适用权重按规则重新归一化。
- 语义分使用校准后的相关指标；不能把原始余弦相似度直接标作“录用概率”。
- 初版不让大模型直接自由打总分；数值评分由版本化规则计算，模型只整理受约束的解释。
- 展示分项、证据、缺口和未知信息；不使用性别、年龄、婚育等属性评分。
- 权重与算法版本进入匹配快照，档案／JD 更新后旧结果标注过期。

### 8.2 RAG 检索

流程：校验权限 → 解析查询与允许的筛选条件 → BGE-M3 向量化 → Top-K 检索 → SQL 回查版本与权限 → 证据筛选 → 生成带引用的回答 → 校验引用确实在返回证据中。
建议初始 K=10、最多 5 名人选；阈值通过虚构标注集校准，不能凭模型自报置信度。

支持问题：按技能／项目查人、解释某人的相关经历、比较岗位要求与档案证据。
不支持无依据的心理／品行判断。无充分证据返回“未找到足够依据”，不填补经历。
引用返回 document_id、document_revision、chunk_id、原文摘录、页码或段落号；原文件访问仍经过权限检查。
对照组需同时测试关键词不一致但语义相关的查询，展示相对传统关键词筛选的价值。

## 9. API 设计

统一前缀 /api/v1；错误结构含 code、message、request_id，不回传模型网关原始敏感错误。
分页采用 page/page_size（上限 100）；任务与资源使用 UUID。
所有端点在控制器校验权限；不接受前端任意 Cypher、SQL、Milvus 表达式或文件路径。

| 方法与路径 | 目的／关键响应 |
| --- | --- |
| POST /auth/login | 用户名密码；建立 JWT；返回用户与角色摘要 |
| POST /auth/logout | 失效当前 jti、清除 Cookie |
| GET /auth/me | 当前用户、角色与权限 |
| GET, POST /users | 管理员分页查账号／建账号 |
| PATCH /users/{id} | 管理员启停、角色分配；增加 auth_version |
| POST /resumes | multipart 文件；202 + task_id、document_id |
| GET /tasks/{id} | state、stage、错误摘要与进度 |
| POST /tasks/{id}/retry | 仅失败可重试任务；保持幂等性 |
| GET /talents | 分页、标签与允许的结构化筛选 |
| GET, PATCH, DELETE /talents/{id} | 档案、编辑触发重建、软删除 |
| GET /resumes/{id}/download | 权限校验后短期签名 URL |
| GET /talents/{id}/evidence | 当前版本字段的原文依据 |
| GET, POST /tags | 列出／创建标签 |
| PUT /talents/{id}/tags | 设置关联标签 |
| GET, POST /jobs | 查岗位／创建 JD 与处理任务 |
| GET, PATCH /jobs/{id} | 查岗位／编辑后重建与缓存失效 |
| POST /jobs/{id}/matches | 岗位找人才；版本化分项与证据 |
| POST /talents/{id}/job-matches | 人才找岗位；相同评分语义 |
| POST /search | question、filters；回答、人才结果、引用 |
| GET /health/live, /health/ready | 进程存活／依赖就绪，不泄露配置 |
| GET /admin/integrations/status | 管理员查看脱敏后的依赖状态 |

创建／修改接口使用请求 DTO，禁止直接把 SQL 模型批量赋值；防止请求修改 is_active、role 等未授权字段。
匹配、检索在依赖不可用时返回明确 503／处理中状态，不用假数据冒充真实结果。
接口示例与完整 OpenAPI Schema 在实现阶段由 FastAPI 导出并纳入验收。

## 10. 本机接入事实与配置约定

2026-11-10 实测：
- 本地 MySQL Windows 服务运行，监听 3306；Docker 引擎可访问。
- 已有 Redis、Neo4j、MinIO、Milvus 容器，Milvus 为 v2.6.24；不能把这些用户环境事实当作所有安装环境的固定版本。
- 对话网关 /v1/models 可访问；auto 推理返回 400 路由错误，zcode-free/glm-5.3-flash 因缺少对应登录账号返回 503。
- 用户指定 Spark-X2.6；大写别名 xingchen/Spark-X2.6 返回路由错误，小写请求标识 xingchen/spark-x2.6 返回 200 和 OK。默认采用已验证的小写标识；仅证明连通性，结构化提取质量仍需测试。
- Ollama bge-m3:latest 的 /api/embed 返回有效、归一化的 1024 维向量。
- 从现有 Linux 容器访问 host.docker.internal:20128/v1/models 和 :11434/api/tags 均返回 200。应用容器仍需部署时回归验证。

| 配置 | 本机开发 | Docker 应用 |
| --- | --- | --- |
| LLM_BASE_URL | http://127.0.0.1:20128/v1 | http://host.docker.internal:20128/v1 |
| LLM_MODEL | xingchen/spark-x2.6（用户指定） | 同左 |
| EMBEDDING_BASE_URL | http://127.0.0.1:11434 | http://host.docker.internal:11434 |
| EMBEDDING_MODEL | bge-m3:latest | 同左 |
| EMBEDDING_DIMENSION | 1024 | 1024 |
| MYSQL_HOST | 127.0.0.1 | host.docker.internal |

密钥、数据库密码、JWT 密钥、对象存储凭据通过未跟踪的本地环境文件／部署 secrets 注入，示例文件仅含占位符。
LLM_API_KEY 不进入 README、日志、任务、客户端或数据库；错误日志统一脱敏。
基础服务的数据库／桶／集合初始化仅针对本项目，不修改其他项目内容。

参考：[Docker Desktop 宿主服务连接](https://docs.docker.com/desktop/features/networking/networking-how-tos/)、[Ollama Embedding API](https://docs.ollama.com/api/embed)。

## 11. 验收与测试建议

| 验收项 | 初始标准 |
| --- | --- |
| 数据与流程 | 30 份虚构简历、5 个 JD；上传、画像、双向匹配、RAG 网页流程可完成 |
| 解析 | PDF/DOCX 均覆盖；无文本、损坏、超限文件被准确拒绝；选定事实字段在标注集准确率目标 ≥90% |
| 证据 | 展示的每条事实引用都能定位到对应版本原文；不存在的字段不生成事实值 |
| 双向匹配 | 至少 5 个岗位各有人工标注候选集；Precision@3 目标 ≥0.8，先冻结标注与规则再测 |
| 检索 | 至少 15 个有答案／无答案问题；可回答问题的人才 Recall@5 目标 ≥0.8；全部引用可定位；无依据问题不编造 |
| 鉴权 | HR 访问管理员 API 返回 403；失效／过期 JWT 被拒；停用与降权生效；下载接口也受保护 |
| 一致性 | 任务重复投递不重复建档；编辑后旧版本不出现在正常结果；部分入库失败可重试恢复 |
| 失败可见 | 模型／Milvus／Neo4j 不可用、队列失败、任务超时有真实错误状态 |
| 性能 | 首版并发 1；上传入队通常 ≤3 秒；单份简历完成目标 ≤120 秒；RAG 目标 ≤30 秒，均在本机记录实测而不预先宣称通过 |
| 部署 | 新应用容器可访问本地 MySQL、网关、Ollama 及全部存储；提供迁移、初始化、启动与验收说明 |

测试分三层：
1. 单元：结构校验、证据定位、评分、权限矩阵、幂等键、状态迁移；模型可替换为确定性测试适配器。
2. 集成：真实 MySQL、MinIO、Redis、Milvus、Neo4j 的项目命名空间；同时覆盖失败与恢复。
3. 端到端：真实模型与本地 BGE-M3，冻结虚构样本集，浏览器完整操作；不能用测试适配器结果替代真实集成验收。

质量阈值是拟定验收标准，需通过本设计审阅；不达标时报告实际结果与原因，不临时降低标准隐藏失败。

## 12. 审阅中待确认的业务边界

以下仅为建议，尚不能记作用户已同意；确认后更新相关表结构、流程与验收项。

1. 硬条件：明确不满足者排除；信息缺失者保留在“需复核”分组，不推断满足，也不混入已确认满足条件的排序。
2. 人工编辑：允许纠正有原文依据的解析字段，并绑定证据；自由补充内容另存“HR 备注”，首版不进入匹配与 RAG 事实证据。重新解析重建事实字段，保留独立的 HR 备注，变更可追溯。

## 13. 实施顺序与阶段产物

设计审阅后，再编写开发计划和可勾选 TODO，建议按以下依赖顺序组织：
配置与迁移 → 鉴权及页面骨架 → 文件上传与任务 → 解析证据与画像 → 向量／图同步 → 岗位与双向匹配 → RAG → 演示数据 → 集成测试及部署验收。
本节只给阶段依赖，不代表已进入开发或已完成详细计划。

每一阶段的文档、代码、测试、部署说明及结果都提交到指定仓库。
