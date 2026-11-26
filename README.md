# Git 仓库接入体检（repo-health-check）

输入一个公开 GitHub 仓库链接，自动分析并可视化：

- **仓库类型识别**：软件项目 / 文档仓库 / 数据仓库 / 空仓库（不同类型用不同评分尺子）
- **README 完整度**：是否存在、是否含安装 / 使用 / 许可证章节
- **依赖清单**：`package.json` / `requirements.txt` / `pyproject.toml` / `go.mod` / `Cargo.toml` 等，统计真实依赖数
- **启动脚本 / 入口**：Makefile、Dockerfile、package.json scripts、`main.py`、CI 配置
- **Windows 专用脚本**：`.bat` / `.cmd` / `.ps1` / `.vbs`
- **硬编码路径**：`C:\`、`/Users/`、`/home/`、`/opt/` 等绝对路径，按代码 / 注释 / 脚本分级
- **密钥与敏感信息**：GitHub / GitLab / Slack / Google / AWS / 腾讯云 / OpenAI / Anthropic 等密钥格式、JWT、Bearer、PEM 私钥、`.env` 入库、明文凭据
- **深度扫描**：
  - gitleaks 第二引擎（若本机有 `gitleaks` 则自动合并其 provider 命中，丢弃误报高的 generic 规则）
  - Git 历史（最近 N 个 commit 新增内容里出现过的密钥，含已删除文件）
  - 压缩包（zip/jar/tar…）内部内容
  - Base64 / URL 编码递归解码（深度 2）
- **自定义规则与基线**：仓库根目录 `.repohealth.json` 可定义内部密钥格式、忽略已知误报

输出**接入就绪度评分**（0–100）、**主要风险清单**（每条带 `file:line`、置信度、命中原因），可选接入 DeepSeek API 生成接入判断与建议。

## 题目需求对照

| 题目要求 | 实现 | 位置 |
|---|---|---|
| Web 页面 + 输入公开 GitHub 链接 | FastAPI + 静态页面，URL 解析/clone 失败有中文提示 | `app/main.py`、`static/` |
| README / 依赖清单 / 启动脚本完整度 | 标题级解析、9 类清单+锁文件、脚本/入口/仅 CI 分级 | `app/scanners.py` |
| Windows 脚本 `.bat/.cmd/.ps1/.vbs` | 全量检出，权重 5 分（不歧视 Linux 项目） | `app/scanners.py`、`app/scoring.py` |
| 硬编码路径 | `C:\`、`/Users/`、`/home/`、`/opt/` 等，按 脚本/代码/注释/容器 分级 | `app/scanners.py` |
| 密钥与敏感信息 | `sk-`、`AKID`、`Bearer`、PEM 私钥、`.env` 入库、明文凭据 + 13 类 provider | `app/scanners.py` |
| 可视化 + 接入就绪度评分 + 风险清单 | 评分环、分项条、风险表（file:line/置信度/原因）、分类型 rubric | `static/`、`app/scoring.py` |
| 加分项：AI 评分/建议 | DeepSeek：中低危复核 + 接入判断 + 建议，与规则评分互补 | `app/agent.py`、`app/ai.py` |
| AI_USAGE.md（含 AI 答错记录） | 11 条记录，含 4 条「AI 答错被数据/对比抓出」 | `AI_USAGE.md` |
| 更新跟踪设计（发现/增量/commit 标注/兜底） | 四问四答 | 本文件「更新跟踪设计」 |

## 技术栈

- 后端：Python 3.12 + FastAPI + uvicorn
- 前端：原生 HTML/CSS/JS（无构建步骤）
- 仓库获取：`git clone --depth 1 --filter=blob:limit=1m`（浅克隆 + 跳过 1MB 以上大文件），分析后清理
- AI（可插拔）：DeepSeek API（OpenAI 兼容协议）

## 运行

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

浏览器打开 http://127.0.0.1:8000 。

## 可选：启用 Agent 复核 + AI 建议

```bash
# PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:DEEPSEEK_MODEL   = "deepseek-chat"   # 可选
```

- 同时启用 **Agent 复核**（medium/low 发现）与 **AI 接入建议**，同一次 API 调用完成。
- 无 key / 调用失败 / 超时 → `ai: null`，跳过复核，输出纯规则结果，**不阻塞主流程**。
- 被 Agent 判为误报的中低危发现会从评分中移除，但仍在风险列表末尾以低危展示，便于人工核对。

## 深度扫描配置

全部通过环境变量控制，默认开启（历史扫描 50 个 commit）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `REPO_HEALTH_HISTORY_DEPTH` | `50` | 历史扫描的 commit 数，`0` 关闭；>0 时克隆改为 `--depth=N` 并关闭部分克隆 |
| `REPO_HEALTH_GITLEAKS` | `1` | 合并 gitleaks 命中；找不到二进制则自动跳过 |
| `REPO_HEALTH_ARCHIVES` | `1` | 扫压缩包（单条目 ≤256KB、总计 ≤20MB、≤500 条目） |
| `REPO_HEALTH_DECODE` | `1` | Base64 / URL 解码递归（深度 2） |
| `REPO_HEALTH_CUSTOM_RULES` | `1` | 应用 `.repohealth.json` |
| `GITLEAKS_PATH` | — | 指定 gitleaks 可执行文件；默认找 `eval/tools/gitleaks.exe` 或 PATH |

### 自定义规则 / 基线

仓库根目录放 `.repohealth.json`：

```json
{
  "rules": [
    {"name": "公司内部 key", "pattern": "MYCO-[A-Z0-9]{24}", "kind": "公司密钥", "confidence": "high"}
  ],
  "ignore": [
    {"file": "tests/fixtures/keys.py", "match": "AKIA", "reason": "测试夹具，已确认非真实"}
  ]
}
```

- `rules`：追加检测，模式编译失败会自动跳过（长度上限 300，最多 50 条）
- `ignore`：按 `file` 匹配，可再加 `match`/`line` 精确限定；命中后从结果移除并计入「基线忽略」

### 历史泄露

单独列出、**不计分**（多为已轮换的旧密钥，且计分会破坏结果可复现性）。风险表里类别为「历史泄露」，带引入的 commit 短 hash。

## 评分模型（按仓库类型选择）

### 软件项目（满分 100）

| 维度 | 分值 | 判定 |
|---|---|---|
| README 完整度 | 20 | 存在 + 安装 / 使用 / 许可证章节 + 字数 |
| 依赖清单 | 20 | 清单文件数 + 锁文件 |
| 启动入口 | 20 | 脚本 / 入口 / package.json scripts 满分；仅有 CI 给一半 |
| Windows 脚本 | 5 | 有 `.bat/.cmd/.ps1/.vbs` 得分（只占 5 分，避免歧视 Linux 项目） |
| 硬编码路径 | 15 | 脚本内路径扣 4/条，代码路径扣 2/条，注释不扣分 |
| 密钥风险 | 20 | 高置信度扣 10/条，中置信度扣 4/条，待确认（low）不扣分 |

### 文档仓库（满分 100）

README 结构 40 + 文档导航 15 + 许可证 10 + 使用示例 20 + 敏感信息 15。
不考核依赖清单和启动脚本——文档仓库没有这两样是正常的。

### 数据 / 模型仓库（满分 100）

README / 数据卡 30 + 数据说明 20 + 许可证 15 + 处理脚本 20 + 敏感信息 15。

### 空仓库 / 未分类

不给分数（`score: null`），只输出事实清单。

等级：≥80 优 / 60–79 良 / 40–59 中 / <40 差。

## 检测置信度

每条发现带 `confidence`：

- `high`（确证）：命中严格格式（`ghp_`、`AKIA`、完整 PEM、`.env` 入库等），参与扣分
- `medium`（疑似）：高熵明文凭据、JWT、Bearer，参与扣分
- `low`（待确认）：注释中的凭据、低熵字符串，只展示不扣分

文档 / 示例 / 测试目录中的命中单独归为「文档示例」，不计入评分。

## 基准评测

`eval/` 目录含 50 个真实仓库的评测框架与结果：

```bash
python eval/run_eval.py              # 全量（克隆缓存到 eval/cache）
python eval/run_eval.py --limit 10   # 只跑前 10 个
python eval/run_eval.py --only rust  # 按 URL 关键字过滤
```

评测指标：类型识别准确率、Windows 脚本检出准确率、各类型分数分布、失败列表。
完整数据见 [`eval/REPORT.md`](eval/REPORT.md)。

另有注入缺陷召回测试：

```bash
python eval/recall_test.py           # 规则层
python eval/recall_test.py --agent   # 含 Agent 复核存活率（消耗 API）
```

21 类缺陷 × 3 个真实仓库 = 63 次注入，**规则层 63/63 检出**，Agent 复核**误降级 0**。
数据见 [`eval/RECALL.md`](eval/RECALL.md)。注意它测的是「已知格式检出能力」，不代表全部召回率。

与 gitleaks 8.30.1 对比（`python eval/compare_gitleaks.py`，需自行下载 `gitleaks.exe` 到 `eval/tools/`）：

| 维度 | 本项目 | gitleaks |
|---|---|---|
| 注入召回（12 类 provider × 3） | **36/36** | 33/36（缺 Anthropic 规则） |
| 8 个真实仓库的「真实」误报 | **0** | 约 10+（generic 规则） |
| 文档/测试上下文识别 | 有 | 无 |
| Git 历史 / 压缩包解码 / 自定义规则 | 无 | 有 |

详见 [`eval/COMPARE.md`](eval/COMPARE.md)。结论：召回取并集，精确靠上下文分类，两者互补而非替代。

> 50 仓库评测基线**不含**深度扫描（gitleaks / 历史 / 压缩包 / 编码 / 自定义规则）——这些在 Web 端默认开启，会增加命中量。评测报告衡量的是规则层本身的稳定性。

### 评测结果摘要（规则层）

| 指标 | 结果 |
|---|---|
| 成功 / 总数 | 49 / 50（失败项为不存在的仓库，错误提示正确） |
| 类型识别准确率 | **41 / 41 = 100%** |
| Windows 脚本检出 | **4 / 4 = 100%** |
| 分数中位数 | **88**（p90 = 94） |
| 真实密钥残留 | **2 条 / 49 仓库**（人工确认均为真实） |
| 硬编码路径 | 194 条计入评分，961 条文档/测试示例不计分 |
| 人工抽检 | high 严重度 6/6 真实，medium 约 70% |

## 两级审核：确定性规则 + Agent 复核

高置信度问题由正则直接定稿；不确定的交给 Agent，避免「规则误报污染分数、或漏报造成虚假安全感」。

| 层级 | 处理对象 | 谁来做 | 是否影响分数 |
|---|---|---|---|
| 第一层 | 高危格式（`ghp_`/`AKIA`/完整 PEM/`.env` 入库/脚本绝对路径） | 正则 + 熵 + 白名单 | 是（规则分） |
| 第二层 | medium/low 发现（疑似明文凭据、代码中的绝对路径） | DeepSeek Agent 复核 + 接入建议 | 仅「判为误报」的中低危会被移除；**高危永不被 Agent 降级** |

Agent 复核与「AI 接入建议」**共用同一个 DeepSeek 客户端、同一次 API 调用**：

```json
{"review": [{"id": "s3", "verdict": "false_positive", "reason": "测试夹具中的假 token"}],
 "verdict": "需整改后接入", "reason": "...", "advice": ["..."]}
```

- 每条候选发现附带命中行上下文（最多 5 行、240 字符），模型只能依据该片段判断，证据不足须标 `uncertain`。
- 页面上每条被复核的风险带标签：`AI 已确认` / `AI 存疑` / `AI 判误报`；判误报的条目降为低危列在末尾，不参与扣分。
- 无 `DEEPSEEK_API_KEY` 时整层跳过，输出纯规则结果——评测报告就是纯规则基线，可复现。

> 隐私提示：启用 Agent 后，命中行的代码片段会发送到 DeepSeek API。只分析公开仓库，但请自行评估代码敏感性。

## 已知局限

- **启发式检测，不是安全工具**。密钥 / 路径检测基于正则 + 熵，无法保证零误报或零漏报；高置信度项可直接处理，中低置信度项请按 `file:line` 人工复核。
- **文档中的示例密钥**：工具会区分文档 / 测试目录，但无法识别所有示例（例如 docstring 里的安装路径示例仍可能被列为低危）。
- **私有 / 超大仓库**：仅支持公开仓库；文件数超过 10 万或克隆超时的仓库会明确报错。
- **更新跟踪**：当前每次分析都会重新克隆，不做增量；增量设计见下节。

## 更新跟踪设计（未实现，设计说明）

**1. 怎么发现更新**
- 定时任务（或用户点「刷新」）调用 GitHub API `GET /repos/{owner}/{repo}`，比较 `default_branch` HEAD `sha` 与已存报告的 `commit_sha`。
- 进阶：GitHub Webhook（`push` 事件）被动触发。

**2. 怎么避免全量重算**
- 缓存「commit sha + 每个文件的 sha1」。
- 更新时 `git fetch` 后 `git diff --name-only old..new`，只对变更文件重跑检测器，未变文件复用结果。

**3. 报告如何标注所基于的 commit**
- 报告头部展示「基于 commit `abc1234`」，当前实现已记录 `commit_sha` 并在前端显示。

**4. 失败 / 限流兜底**
- GitHub API 限流（403）：保留上次报告 + 指数退避重试；用 `If-None-Match` / `ETag` 节省配额。
- 克隆失败（私有化 / 删除）：返回缓存报告并标注 `stale`。

## API

- `GET /` — 体检页面
- `POST /api/analyze` — 请求体 `{"repo_url": "https://github.com/owner/repo"}`

响应关键字段：

```json
{
  "ok": true,
  "owner": "tiangolo", "repo": "fastapi",
  "commit_sha": "…",
  "elapsed_ms": 8100,
  "scan": { "readme": {...}, "deps": {...}, "startup": {...}, "signals": {...},
            "windows_scripts": [...], "hardcoded_paths": [...], "secrets": [...],
            "stats": {"scanned_files": 1234} },
  "score": {
    "archetype": {"type": "software", "label": "软件项目", "confidence": "high", "evidence": [...]},
    "score": 88.0, "grade": "优",
    "components": {"readme": {"name": "README 完整度", "score": 20, "max": 20}, ...},
    "risks": [{"severity": "high", "confidence": "high", "file": "...", "line": 12, "detail": "...", "reason": "..."}],
    "summary": {"secrets": 0, "hardcoded_paths": 1, "doc_findings": 47, "files_scanned": 1234}
  },
  "ai": null
}
```

## 测试

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

38 个用例，覆盖：README 标题解析、依赖计数（npm/pip/Cargo/Gemfile/pom/csproj）、启动脚本 vs 仅 CI、Windows 脚本、硬编码路径分级与过滤、密钥 provider 格式 / 占位符 / 假密钥 allowlist、类型识别（software/docs/data/empty）、分类型评分、结果确定性，以及 Agent 复核边界（中低危可降级、高危永不降级）。

## 目录结构

```
repo-health-check/
  app/
    main.py        # FastAPI 路由（并发上限 3）
    git_ops.py     # URL 解析 + 浅克隆 + 大小/错误处理
    scanners.py    # 检测器 + 类型信号 + 置信度
    classify.py    # 仓库类型识别
    scoring.py     # 分类型 rubric + 风险清单 + Agent 判定应用
    agent.py       # Agent 复核（medium/low）+ 接入建议，一次调用
    ai.py          # DeepSeek 客户端（唯一入口）
    engines.py     # gitleaks 第二引擎（可选）
    deepscan.py    # 压缩包 / Base64 / URL 解码
    history.py     # Git 历史泄露
    customrules.py # .repohealth.json 规则与基线
    models.py
  static/          # 页面
  tests/           # 30 个离线用例
  eval/            # 50 仓库评测 + 召回 + gitleaks 对比
  requirements.txt
  AI_USAGE.md
```
