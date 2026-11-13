# 指标口径与分析实验平台

面向业务指标定义、口径管理、聚合查询、分群分析、A/B实验设计、分析方案管理与数据服务发布的数据分析工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径 | `metric` | 指标定义 | 指标编号、指标名称、计算公式 |
| 聚合查询 | `queryboard` | 查询模板 | 模板编号、指标名称、维度字段 |
| 分群分析 | `segment` | 人群分群 | 分群编号、分群名称、筛选规则 |
| 实验设计 | `abtest` | 实验方案 | 方案编号、实验名称、对照组定义 |
| 分析方案 | `analysis` | 分析方案 | 方案编号、分析主题、数据集范围 |
| 看板配置 | `dashboard` | 看板页面 | 看板编号、看板名称、包含指标 |
| 数据服务 | `delivery` | 服务版本 | 服务编号、数据集名称、查询参数定义 |
| 漏斗分析 | `funnel` | 漏斗定义 | 漏斗编号、漏斗名称、步骤序列 |
| 同期群分析 | `cohort` | 同期群定义 | 群组编号、群组名称、划分事件 |
| 归因模型 | `attribution` | 归因规则 | 规则编号、归因类型、回溯窗口天 |
| 指标预警 | `alertconfig` | 预警规则 | 预警编号、关联指标、比较运算符 |
| 报表调度 | `reportsch` | 报表任务 | 任务编号、报表名称、包含指标 |
| 维度属性 | `dimprop` | 维度属性 | 属性编号、维度名称、属性名称 |
| 多维下钻 | `datadrill` | 下钻路径 | 路径编号、起始维度、下钻层级 |
| 预测模型 | `forecast` | 预测方案 | 方案编号、预测目标、算法名称 |
| 标签管理 | `tagmgr` | 标签定义 | 标签编号、标签名称、标签分类 |
| 洞察推送 | `insight` | 洞察报告 | 报告编号、报告标题、分析主题 |
| 数据验证 | `dataval` | 验证任务 | 任务编号、验证数据集、验证类型 |

## 归因数据服务：契约发布与分页读取

归因规则（`attribution`）与对外数据服务（`delivery`）共享同一份契约和结果版本。

- **契约键**：`规则编号@规则版本` 集合 + 返回字段（集合语义）+ 请求参数定义（键排序后比较）。
  契约键相同的重复发布复用同一服务版本（只保留一份，`复用次数` 累加）；
  返回字段或请求参数变化才生成新服务版本，并让旧契约下的页段结果失效。
  速率限制、单次返回上限、消费方应用是运行属性，不参与契约键。
- **批量发布**：`POST /api/attribution/publish/batch`，`values.规则编号列表` 多选规则，
  契约字段（服务编号、返回字段、查询参数定义、速率限制、分页最大条数、消费方应用）
  整组齐全才允许发布；响应逐条给规则结论。
  单条规则动作也支持多选：`POST /api/attribution/batch/actions`。
- **分页读取**：`POST /api/delivery/read`，首段 `页码=1`，后续页段带同一 `会话编号` 按固定次序读取。
  单次 `条数` 超过契约的 `分页最大条数` 在请求入口直接拒绝（HTTP 400），不会等到最后一页；
  跳页、换条数、跨契约续读都会被拒绝。
- **换版与不可用**：规则换版后旧契约 / 旧会话明确返回「请从首段重新开始」（HTTP 409）；
  规则下线或不可用时读取返回 HTTP 503 且 `items` 为空，不会顶用上一次的归因结果。
- **并发与幂等**：`values.当前版本` 与 `values.请求编号` 参与校验。同一期望版本并发发布只接受一个，
  另一个记入冲突版本；相同请求编号重复提交返回首次结果，不生成第二个版本 / 第二个页段。
- **验收视图**：`GET /api/delivery/stats`（契约数量、契约复用数量、失效页段、冲突版本）、
  `GET /api/delivery/contracts`、`GET /api/delivery/segments`、`GET /api/delivery/conflicts`。
- **审计入口**：`GET /api/attribution/audit/logs` 与 `GET /api/delivery/audit/logs`，
  每条记录带规则版本（如 `ATTR-0003@v1`）。

示例：`DELI-0003` 单次返回上限 200、消费方每天 60 次。相同契约再次发布复用 v1；
把返回字段从「日期,渠道,归因规则编号,触点数量,转化数量,转化金额」改为
「日期,渠道,归因规则编号,转化金额」后生成 v2，并让 v1 的页段失效。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 契约规范化与契约键计算在 `backend/app/contracts.py`；跨模块共享的契约 / 页段 / 冲突状态在
  `backend/app/contract_state.py`；发布与读取的审计记录在 `backend/app/audit.py`。

领域：数据工程
子方向：数据分析与数据应用
