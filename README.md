# 特征工坊 · 特征工程与标注数据管理平台

面向特征注册与检索、衍生特征工程、时间窗口计算、特征回溯填充、样本分层抽取、标注任务派发、标注质量审核、特征血缘追踪与在线/离线特征服务的数据特征工程与标注管理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 特征注册 | `feature_reg` | 特征定义 | 特征编号、特征名称、数据类型 |
| 衍生工程 | `derive_rule` | 衍生规则 | 规则编号、输入特征、衍生函数 |
| 时间窗口 | `time_window` | 窗口模板 | 模板编号、窗口类型、窗口宽度 |
| 特征回溯 | `backfill_job` | 回溯任务 | 任务编号、目标特征、起始日期 |
| 样本分层 | `sample_strata` | 分层策略 | 策略编号、特征范围、分层字段 |
| 标注派发 | `label_task` | 标注任务 | 任务编号、样本范围、标注类型 |
| 标注审核 | `label_review` | 审核批次 | 批次编号、标注任务、审核人 |
| 一致性计算 | `consent_calc` | 一致性报告 | 报告编号、标注任务、评估指标 |
| 特征存储 | `feature_store` | 存储配置 | 配置编号、特征列表、存储引擎 |
| 在线服务 | `online_serv` | 服务端点 | 端点编号、特征列表、QPS上限 |
| 离线导出 | `offline_exp` | 导出管道 | 管道编号、特征范围、导出格式 |
| 特征血缘 | `feature_line` | 血缘链路 | 链路编号、上游特征、下游特征 |
| 特征监控 | `feature_mon` | 监控规则 | 规则编号、目标特征、监控类型 |
| 版本管理 | `version_mgr` | 特征版本 | 版本编号、关联特征、版本标签 |
| 数据切分 | `data_split` | 切分配置 | 配置编号、数据集、切分方式 |
| 特征目录 | `feat_catalog` | 目录索引 | 索引编号、目录名称、父目录 |
| 质量规则 | `quality_rule` | 质量检查 | 规则编号、目标特征、检查类型 |
| 质量放行 | `quality_release` | 放行记录 | 放行编号、入库批次、结论、可读分区 |
| 标注词典 | `label_dict` | 标注项 | 词典编号、标签名称、标签值 |
| 批量接入 | `batch_load` | 接入任务 | 任务编号、数据源、目标特征 |
| 特征报告 | `report_task` | 分析报表 | 报告编号、报告类型、特征范围 |
| 调度编排 | `schedule_job` | 调度计划 | 计划编号、任务类型、关联对象 |
| 治理审计 | `govern_audit` | 审计记录 | 记录编号、审计对象、审计类型 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖

## 质量放行（质量规则的分区交付能力）

质量规则只描述阈值，质量放行负责把「待入库批次」按分区校验后交付存储。
核心算法在 `backend/app/domain/quality_release.py`（纯函数、无副作用），
接口在 `backend/app/routers/quality_release.py`，持久化在
`backend/app/services/quality_release.py`。

- **一份适用规则 + 一个放行范围**：每次提交冻结规则编号、版本、阈值、检查字段，
  以及本次参与判定的分区列表；提交后记录不可回改。
- **阈值只追加版本**：改阈值必须发布新版本，旧放行记录保留其冻结快照；
  历史记录如需按新阈值判定，只能重新提交，不会被悄悄改成通过
  （`重新复核` 只给出「若按新版本会翻转哪些分区」的提示）。
- **分区级结果**：每个分区记录抽样分母、空值/异常值实测比例、阈值、阻断级别、
  判定与拒绝证据（可回到具体分区和字段）。
- **空分区不放行**：抽样分母为 0 记「无样本」并拒绝；即使规则级别是「告警」，
  空分区也按分区阻断处理。
- **恰好等于阈值算通过**：阈值比较使用 `Decimal`，避免浮点误差。
- **整批 vs 部分**：默认按分区阻断，末区超阈时结论为「部分放行」并给出可读分区；
  规则配置「阻断整批」时结论为「整批扣住」，本地通过的分区也收回可读性。
- **同步存储**：放行记录的 `存储清单` 只列可读分区，并按清单回写特征存储的
  `可用分区 / 不可用分区 / 存储写入状态`，整批扣住时不写入任何分区。

接口：

```text
GET  /api/quality-release/rules                     规则版本列表
POST /api/quality-release/rules                     追加新规则版本
POST /api/quality-release/releases/preview          只算不落库，核对可读分区
POST /api/quality-release/releases                  提交放行记录
GET  /api/quality-release/releases                  放行记录列表
GET  /api/quality-release/releases/{no}             放行详情（分区结果 + 拒绝证据）
GET  /api/quality-release/releases/{no}/manifest    存储清单
GET  /api/quality-release/releases/{no}/recheck     按冻结阈值复核（新规则仅提示）
```

测试：`cd backend && .venv/bin/python -m pytest tests -q`。
