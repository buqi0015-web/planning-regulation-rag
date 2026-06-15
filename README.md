# 城市规划法规 RAG 知识库

这是一个面向城市规划管理场景的法规 RAG 智能检索与辅助问答系统，重点解决四个问题：

1. 法规不能随意按固定长度切碎，需要优先按“章、条”分块。
2. 法规名称、文号和条款号需要精确匹配，同时用户又常使用口语化表达，因此采用混合检索。
3. 旧版本或已废止法规不能参与默认回答，因此检索阶段必须过滤状态并保留版本元数据。
4. 回答必须能追溯到法规条款，并明确提示不能替代正式审批意见。

公开仓库中的 `data/source` 为演示材料，不包含项目使用的真实法规资料。

## 项目效果

项目基于 100 道冻结分层测试集进行检索评测。在不更换测试集和 Embedding 模型的情况下，通过条款号精确匹配、法规名称加权，以及表格和图示意图路由优化，实现：

| 指标 | 原始基线 | 优化后 |
|---|---:|---:|
| Recall@5 | 52.00% | 84.00% |
| MRR | 45.96% | 77.01% |
| Top-1 条款准确率 | 40.00% | 72.00% |
| nDCG@5 | 44.16% | 76.76% |

测试集与评测结果位于 `data/eval`。指标衡量检索效果，不等同于最终回答准确率。

## 架构

```text
Markdown/TXT 法规
  -> Frontmatter 元数据解析
  -> 按法规条文分块
  -> SQLite 文档库 + FTS5 全文索引 + text-embedding-v4
  -> 语义向量 + 关键词 + BM25 三路召回
  -> 加权 RRF + 条款号/内容类型/状态/地区业务重排
  -> 摘录式回答或可选 LLM 回答
  -> 引用来源 + 冲突提示 + 免责声明
```

默认配置使用本地 Hash 向量，便于零成本跑通链路；项目正式评测使用 `text-embedding-v4`。生成模型使用 `Qwen-Plus`，视觉模型可配置 `Qwen3-VL-Flash`。Cross-Encoder Reranker 尚未接入，需要通过冻结测试集验证收益后再决定。

## 快速开始

首次使用百炼 API：

1. 打开项目根目录的 `.env`，在 `DASHSCOPE_API_KEY=` 后填入你的 Key。
2. 运行 `python -m app.cli check-api`，检查 Embedding 与生成模型是否可用。
3. 检查成功后，将 `.env` 中的 `EMBEDDING_PROVIDER=hash` 改成 `EMBEDDING_PROVIDER=openai_compatible`。
4. 重启 API 服务并重新运行 `python -m app.cli ingest`，使用真实 Embedding 重建索引。

`.env` 已被 `.gitignore` 排除。不要将 Key 写入 `.env.example`、代码或文档。

```powershell
cd city-planning-rag
python -m pip install -r requirements.txt
python -m app.cli check-api
python -m app.cli ingest
python -m app.cli search "高层建筑沿主干路退让多少米"
python -m app.cli ask "申请建设工程规划许可证需要哪些材料？"
python -m app.cli eval --dataset data/eval/formal_retrieval_v1.jsonl --top-k 10
python -m unittest discover -s tests -v
```

启动 API：

```powershell
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000` 查看面试演示工作台；打开 `http://127.0.0.1:8000/docs` 查看 API 文档。

- `POST /ingest`：增量索引法规
- `POST /search`：查看召回条文
- `POST /ask`：获得带引用回答
- `GET /health`：查看文档与分块数量

## 自己加入真实法规

从政府官网获取现行有效文件并核验来源，将内容整理为 UTF-8 Markdown：

```markdown
---
title: 法规名称
document_no: 文号
authority: 发布机关
authority_weight: 1.0
jurisdiction: 适用地区
effective_date: 2026-01-01
status: 现行有效
version: 2026版
source_url: 官方网页地址
---

第一条 条文内容……
第二条 条文内容……
```

然后执行 `python -m app.cli ingest`。只有内容哈希发生变化的文档才会重新索引。

## 从 MVP 到生产版

| 阶段 | 要完成的能力 | 验收指标 |
|---|---|---|
| MVP | 条文分块、混合检索、引用、增量更新 | Recall@5 >= 0.80 |
| v1 | PDF/OCR、同义词、地区与效力层级过滤 | Recall@5 >= 0.90 |
| v2 | 中文 Embedding、Reranker、问题改写 | 答案引用正确率 >= 0.95 |
| v3 | 多版本冲突检测、权限、监控、Bad Case 闭环 | 过期法规误用率 = 0 |

## 产品指标

- 核心业务指标：法规查询完成率、人工检索时间节省比例。
- 技术指标：Recall@5、MRR、引用正确率、过期法规误用率、P95 响应时间。
- 体验指标：回答有用率、用户追问率、用户纠错率。
- 安全指标：无依据回答率、错误适用地区率、未提示冲突率。

## 学习顺序

1. 先运行 `ingest`，观察一篇法规被切成多少条。
2. 运行 `search`，检查正确条款是否进入 Top 5。
3. 运行 `eval`，用 Recall@5 与 MRR 判断检索好坏。
4. 故意加入一份旧版或废止法规，验证默认检索是否过滤。
5. 配置 OpenAI 兼容服务，比较“召回正确”和“最终回答正确”的差异。

## 面试准备

- [正式评测报告](docs/正式评测报告_v1.md)
- [真实项目拆解与技术选型](docs/真实项目拆解与技术选型.md)
- [从 0 到 1 搭建指南](docs/从0到1搭建指南.md)
