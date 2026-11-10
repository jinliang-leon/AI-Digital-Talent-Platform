# 项目协作规则

## 项目与交付位置

项目名称：AI数字化人才平台。
唯一 GitHub 交付仓库：https://github.com/jinliang-leon/AI-Digital-Talent-Platform

需求、设计、计划、代码、测试和部署说明均在本仓库维护。当前业务基线见 [docs/requirements.md](docs/requirements.md)。

## 不可擅自变更的约束

- Python、FastAPI、JWT 鉴权、MVC 架构。
- LangChain + LangGraph。
- 用户本地 MySQL、Neo4j、Milvus、Redis、MinIO。
- Docker 容器化部署；简历原文件存放于 MinIO。
- 第一版面向课程／作品演示，业务用户为企业 HR。
- 允许选择部分功能实施；首版范围以确认后的需求文档为准。
- 依次完成需求、架构与数据库及接口设计、开发计划与 TODO、开发、测试验收。
- 将已确认需求、工程建议和待确认事项明确区分，不能把建议记作用户决定。

## 当前阶段

Q1–Q10 需求访谈完成，完整设计已形成，待用户审阅确认。设计见 [首版设计](docs/superpowers/specs/2026-11-10-talent-platform-design.md)。
设计整体确认后，先形成开发计划与 TODO，再实施。

## 额外已确认业务规则

- 默认对话模型 Spark-X2.6，网关请求标识 xingchen/spark-x2.6；Embedding 为本地 Ollama bge-m3:latest，1024 维。
- 岗位硬条件明确不满足者排除；信息缺失者保留在独立“需复核”分组。
- 事实字段修改需关联原文证据；HR 备注独立存储，不进入匹配与 RAG；重新解析保留备注。
- 密钥、密码和签名密钥只通过本地环境／部署 secrets 注入，不提交真实凭据。
