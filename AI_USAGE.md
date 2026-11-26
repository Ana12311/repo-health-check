# AI 使用说明（AI_USAGE）

本项目使用 AI Coding 工具辅助开发，本文件说明用了哪些 AI 工具、如何验证其输出，并记录至少一条「AI 答错但被发现」的实例。

## 用了哪些 AI 工具

| 工具 | 用途 |
|---|---|
| Claude Code（AI Coding 工具，底层模型由环境配置） | 需求拆解、代码生成、正则设计、测试用例编写、计划制定 |
| DeepSeek API（`deepseek-chat`） | 运行时两层能力，**同一个客户端、同一次调用**：① 复核 medium/low 发现（confirmed / false_positive / uncertain）；② 输出接入判断与 3–5 条建议 |

DeepSeek 走 OpenAI 兼容协议（`base_url=https://api.deepseek.com`），仅在有 `DEEPSEEK_API_KEY` 时启用，无 key / 失败 / 超时自动降级为纯规则评分，不阻塞主流程。

## 如何验证 AI 输出

1. **检测器（Claude 生成的代码）**：每个检测器都有对应的 `pytest` 单测（`tests/test_scanners.py`），用手造的样例仓库断言命中路径 / 类型 / 行号。密钥正则额外做了防误报用例。
2. **端到端**：启动服务后用真实公开仓库（`octocat/Hello-World`）跑 `POST /api/analyze`，核对返回的 `scan` / `score` / `commit_sha` 字段。
3. **50 仓库基准评测**：`eval/run_eval.py` 克隆 50 个真实仓库（覆盖 Python / Node / Java / Go / Rust / 文档 / 数据 / 安全工具 / Windows / 空仓库），统计类型识别准确率、分数分布、密钥与路径检出量，并人工复核 top 发现。评测结果见 `eval/REPORT.md`。**AI 生成的检测规则一律以评测数据为准，不以 AI 自述为准。**
4. **AI 建议与 Agent 复核（DeepSeek）**：约束其只输出 JSON（`{"review": [...], "verdict": ..., "reason": ..., "advice": [...]}`），服务端 `json.loads` 解析；解析失败、超时或非 JSON 一律返回 `null`，回落到纯规则结果。提示词要求「只能依据提供的代码片段判断，证据不足必须标 uncertain，不得编造路径」。
5. **Agent 判定必须可被约束**：设定硬边界——高危发现永远不送审、也永远不能被降级（`test_agent_downgrades_medium_but_not_high`）；Agent 判误报的条目仍保留在报告里，只是移出评分。这样即使模型判断错误，最严重的风险也不会被静默隐藏。单测用构造的 verdict 覆盖「确认 / 判误报 / 高危不降级」三类路径，不依赖网络。

## 「AI 答错但被我抓」记录

### 记录 1：`_is_text` 漏掉了无扩展名的文件，导致 README 与私钥双双漏检

**错误**：AI 初版的文本文件判定（`app/scanners.py::_is_text`）只按「已知扩展名 + 少数特殊文件名（Makefile / Dockerfile / .env 等）」分类。结果：

1. `octocat/Hello-World` 的 `README`（**无扩展名**）被当成非文本跳过，端到端扫描返回 `file_count: 0`、`readme.exists: false` —— 明显错误。
2. 私钥文件 `id_rsa`（**无扩展名**）被跳过，`-----BEGIN RSA PRIVATE KEY-----` 检测不到，单测 `test_secrets_detection` 失败。

**发现方式**：不是靠 AI 自查，而是靠（a）真实仓库 e2e 测试时 `file_count=0` 的反常结果，和（b）`pytest` 断言 `私钥` 未命中。

**修正**：给 `_is_text` 增加 `_EXTENSIONLESS_TEXT` 集合（`readme` / `license` / `changelog` 等）和 `_KEY_LIKE_BASES`（`id_rsa` / `id_ed25519` 等）+ 密钥相关扩展名（`.pem` / `.key` / `.pub` / `.crt` / `.cer`）。同时补了 `test_readme_extensionless` 单测防止回归。修正后 11/11 单测通过，Hello-World 正常识别 README。

### 记录 2：`sk-` 正则的误报风险

**顾虑**：`sk-`（OpenAI 密钥前缀）很短，容易撞上普通字符串（如文档里写「sk-xxx 只是一个示例」）。

**处理**：不靠肉眼猜，写 `test_no_false_positive_on_plain_text` 单测验证——`sk-` 后必须跟 ≥16 位字母数字（`{16,}`）才命中，普通说明文字不误报。单测通过确认无此误报。

### 记录 3：硬编码路径正则撞上 URL / docker 卷挂载（用户真实仓库暴露）

**错误**：初版 `app/scanners.py` 的硬编码路径正则有两处缺陷：

1. `[A-Za-z]:/` 没加词边界，把 `http://` 匹配成 `p://`、`https://` 匹配成 `s://`、docker 卷 `mysql_data:/var/lib/mysql` 匹配成 `a:/var/lib/mysql`——URL 和容器卷挂载全被误判成「Windows 盘符路径」。
2. `[A-Za-z]:\\` 正则里 `\\\\` 实际匹配**双反斜杠** `C:\\`，真代码里的单反斜杠 `C:\` 反而漏检。测试用例也用了双反斜杠，掩盖了 bug。

**发现方式**：用户拿自己的两个公开仓库（`Ana12311/CodeMind-AI`、`Ana12311/TaskFlow`）实测，报告「都显示 3 处密钥风险」，反查时发现硬编码路径把 URL 全标红了。单测全是自造样例、没覆盖 URL 场景，所以没抓到。

**修正**：
- `[A-Za-z]:/` 加 `\b` 词边界（`http://` 的 `p` 前面是 `t`、`data:/` 的 `a` 前面是 `t`，都无边界，不再误报）。
- `[A-Za-z]:\\` 改回单反斜杠匹配。
- 补 `test_hardcoded_paths_ignore_urls_and_volumes` 单测防回归。

**附带收获**：密钥检测同时暴露「文档占位符误报」。初版把 `SECRET="<数据库密码>"`、`PASSWORD="123456"`、`TOKEN_TYPE="Bearer"`、`JWT_SECRET="至少32字节的随机串"` 都报成密钥。修正方案：明文凭据的**值**若含 `< >`、非 ASCII（中文占位说明）、或命中 `your/example/changeme/123456/bearer` 等占位词，则跳过。真实密钥几乎全是 ASCII，此启发式安全。修正后两个仓库密钥风险从 3+3 降到 0。最后一条 `REFRESH_TOKEN = "auth:refresh:"` 也处理掉：加一条「小写冒号命名空间」规则（`re.fullmatch(r"[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)+:?", val)`），缓存 key 前缀形如 `auth:refresh:` / `cache:user` 不再误报——真实密钥不会长成这种格式。

### 记录 4：明文凭据正则漏检带下划线前缀的常见写法

**错误**：初版凭据正则 `(password|passwd|secret|api[_-]?key|...)\s*[=:]\s*['"]...` 要求关键字**紧跟** `=`/`:`。结果最最常见的写法全漏检：

- `SECRET_KEY = "realsecret123"` —— `secret` 后面还有 `_KEY`，不匹配
- `DB_PASSWORD = "..."` —— `password` 不在开头，前面是 `DB_`
- `MY_API_KEY = "..."` —— `api_key` 前面是 `MY_`

**发现方式**：本次验证写 `test_secrets_detect_prefixed_credential` 单测，断言 `SECRET_KEY` / `DB_PASSWORD` / `MY_API_KEY` 三条都应命中，结果只命中 2 条（`SECRET_KEY` 漏了），暴露假阴性。

**修正**：关键字后加 `[A-Za-z0-9_-]*`，允许 `_KEY` / `_PASSWORD` 等后缀再跟 `=`。**假阴性比假阳性更危险**——漏报真密钥会给用户错误的安全感，所以优先保证不漏检，误报交给记录 3 的值启发式过滤。

**教训**：测「命中」时不能只用裸关键字（`SECRET=`），要覆盖真实配置的常见变体（`SECRET_KEY` / `DB_PASSWORD` / `MY_API_KEY` / `AWS_ACCESS_KEY_ID` 等）。

### 记录 5：安全工具仓库误报爆表——缺「代码 vs 文档」区分

**现象**：拿 `elementalsouls/Claude-BugHunter`（安全/漏洞研究工具）测试，初版报 **43 处密钥 + 33 处硬编码路径**，几乎全是误报。原因：这仓库的**内容本身就是**假密钥、密钥模式文档、LFI/攻击 payload 路径（如 `C:\Windows\win.ini`、`/var/www/html/shell.php`）。仓库自己就有一份 allowlist（`scripts/lint_skills.py` 的 `SECRET_ALLOW`）：`AKIAIOSFODNN7EXAMPLE|EXAMPLE|wJalrXUtnFEMI|\[0-9A-Z\]|\{1[06]\}|<[^>]+>|\.\.\.`。

**根因**：工具把所有文件一视同仁，没有行业扫描器（gitleaks / trufflehog）的两条标准做法：

1. **值级白名单**：`AKIAIOSFODNN7EXAMPLE`（AWS 官方示例 key）、`wJalrXUtnFEMI`、`...` 截断占位、`${...}` 模板、裸 `-----BEGIN ... PRIVATE KEY-----` 头（无 `END` 的真实 key 必然有 `END`）。
2. **文件级分类**：`docs/` `tests/` `*.md` `references/` `examples/` `skills/` 里的命中是「文档/示例/测试」，不是真实泄露，应降级为低危、不计入接入就绪度扣分。

**发现方式**：用户第三次拿真实仓库测试，`score=69` 且密钥/路径列表全是指标性假数据。反查 `scripts/lint_skills.py` 发现仓库自带 allowlist，正好印证缺这两件事。

**修正**：
- 值级：`_SECRET_FP_RE = r"\.\.\.|EXAMPLE|wJalrXUtnFEMI|\$\{"` 跳过；PEM 要求文件含 `-----END` 才算真私钥。
- 文件级：`_is_doc_or_test` 识别 `.md/.rst/.txt`、`docs/tests/examples/references/skills` 目录、`test_`/`_test`/`_spec` 文件名；命中降级为「低危·文档示例」，不扣分。
- 评分只对「真实代码」里的密钥/路径扣分；文档示例另计 `summary.doc_findings` 并在前端显示「文档示例 N(不计分)」。

**结果**：该仓库 `score 69 → 92`，密钥风险 `43 → 0`，硬编码路径 `33 → 1`（剩 `cbh/cli.py:30` docstring 里的安装示例 `/usr/local/bin/cbh`，属字符串字面量，通用识别成本高，接受为已知残余）。测试 `16 → 17` 个。

**教训**：检测工具必须做「上下文分类」——同一把密钥在 `.md` 文档里是示例，在 `config.py` 里才是泄露。靠「命中即高危」的纯正则，在文档型/安全型仓库上必然误报爆表。

### 记录 6：文档目录白名单只匹配完整目录名，漏掉 docs_src / tutorial

**错误**：AI 写的 `_is_doc_or_test` 用 `seg in _DOC_DIRS` 判断目录，即只有目录名**恰好等于** `docs`、`tests`、`examples` 才算文档/示例。结果 FastAPI 仓库的 `docs_src/`（官方教程示例代码）被当成真实源码：其中 4 处教学用 `SECRET_KEY = "09d25e..."` 被判成真实密钥（中置信度），分数从应有的 88 掉到 69。多个仓库的 `tutorial/`、`docs_src/`、`examples_*` 同理。

**发现方式**：50 仓库评测第一轮跑分，FastAPI 只有 69 分且 `secrets=4`，与「官方最佳实践仓库」的预期明显不符；打开命中列表发现全部来自 `docs_src/security/tutorial004.py`。

**修正**：目录判断改为「完整名集合 + 前缀匹配」——`docs_src` / `examples_xxx` / `tutorial_xxx` 通过 `_DOC_DIR_PREFIXES` 命中；同时补 `tutorial`、`tutorials`、`cookbook`、`__tests__`、`spec` 等目录名。修正后 FastAPI 88 分、真实密钥 0、文档示例 47 条（只展示不计分）。

**教训**：AI 写分类规则时只覆盖了「教科书目录名」，没考虑真实仓库的命名多样性（`docs_src`、`__tests__`、`test-utils`）。这类问题自造 fixture 抓不到，必须用真实仓库评测。

### 记录 7：Windows 中文环境下 git 输出解码崩溃

**错误**：AI 写的 `subprocess.run(..., text=True)` 未指定编码。Windows 简体中文系统默认 GBK，git 输出含非 GBK 字节时子进程读取线程抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xa2`，克隆错误信息丢失。

**发现方式**：评测第一次跑 FastAPI 时终端出现 `Exception in thread Thread-2 (_readerthread)`。

**修正**：所有 git 子进程调用统一加 `encoding="utf-8", errors="replace"`。评测脚本运行时设 `PYTHONIOENCODING=utf-8`。

**教训**：跨平台工具不能依赖系统默认编码；`text=True` 不等于 UTF-8。

### 记录 8：评测数据推翻了「纯正则就够」的假设

**背景**：第二轮要求「随便拉一个仓库结果都要有参考价值」。AI 最初的思路是继续加正则、加白名单。

**被数据推翻**：50 仓库评测显示，收紧规则后高危误报已降到 2 条，但 **medium 级路径发现人工抽检只有约 70% 是真的**——剩下的差在「源码里的测试夹具路径」「程序默认安装路径常量」，正则无法判断语义。同时再收紧会开始漏报真实问题（假阴性比假阳性更危险）。

**修正（架构变化）**：不再追求「一条规则通吃」，改为两级审核：
1. 高危发现由规则定稿，Agent 不得降级；
2. medium/low 交给 DeepSeek Agent 复核（confirmed / false_positive / uncertain），与接入建议共用同一个客户端、同一次调用；
3. Agent 判为误报的条目仍保留在报告（低危），只移出评分，防止模型悄悄抹掉风险。

**验证**：`test_agent_downgrades_medium_but_not_high`、`test_agent_confirmed_keeps_penalty` 两个离线单测锁死上述边界；无 key 时整层跳过，规则基线保持可复现（评测报告即基线）。

**教训**：当规则准确率卡在 70% 左右、继续调参开始伤害召回率时，正确做法是引入语义层，而不是继续堆正则。

### 记录 9：召回测试抓出无扩展名私钥漏检

**错误**：`_is_text` 只按已知扩展名 + 少量白名单文件名（`id_rsa`、`id_ed25519`）判断文本文件。召回测试注入 `deploy/keys/id_deploy`（完整 RSA 私钥）后，**3 个仓库全部漏检**——真实项目里私钥文件经常无扩展名且不叫 `id_rsa`。

**发现方式**：注入缺陷测试是唯一能暴露「漏报」的实验。此前的 50 仓库评测只能看误报（precision），漏报（recall）在没有注入前完全不可见。

**修正**：无扩展名文件读前 64 字节，含 `-----BEGIN` 就纳入扫描。复测召回 100%（63/63），补 `test_extensionless_pem_file_detected` 回归测试。

**教训**：precision 靠真实语料，recall 靠注入缺陷。只做前者会系统性高估系统能力。

### 记录 10：与 gitleaks 对比暴露 AWS 规则过宽

**错误**：本项目 AWS 规则写成 `AKIA[0-9A-Z]{16}`，比真实格式宽——AWS Access Key ID 使用 base32 字符集（`A-Z2-7`，不含 0/1/8/9）。对比时我们"检出"了一个 gitleaks 不认的 token，最初以为是 gitleaks 漏报，实测单文件验证后确认是**我们误报**。

**发现方式**：`eval/compare_gitleaks.py` 在 3 个仓库上都出现「gitleaks 漏检 secret_aws」，与 gitleaks 已知规则能力矛盾；用单 token 文件复现，排除文件类型、关键词、上下文等因素后定位到字符集。

**修正**：规则改为 `AKIA[A-Z2-7]{16}` / `ASIA[A-Z2-7]{16}`，测试与召回脚本的假 token 同步改成合法 base32。修正后双方 AWS 检出一致，gitleaks 剩余差异只有它没有 Anthropic 规则。

**教训**：对标外部工具时，「对方漏检」先怀疑自己。一致性问题往往是自己的规则过宽，而不是对方能力不足。

### 记录 11：四项扩展功能的验证方式

按 gitleaks 对比结论落地了四项能力，每项都有可复现验证：

| 能力 | 验证方式 | 结果 |
|---|---|---|
| gitleaks 第二引擎 | gin 仓库实测：我们跳过 `testdata/` 的私钥，被 gitleaks 引擎补回 | 合并 1 条真实私钥 |
| Git 历史扫描 | 本地 git 仓库「提交密钥→删除→再提交」单测；gitleaks 仓库实测 | 抓到 4 条历史提交密钥 |
| 压缩包 + 编码解码 | 单测：zip 内密钥、Base64 编码密钥 | 均检出 |
| 自定义规则 / 基线 | 单测：自定义 pattern 命中 + ignore 生效计数 | 通过 |

边界设计（刻意为之）：
- 历史泄露**不计分**，避免已轮换的旧密钥让分数不可复现；
- 压缩包限制单条目 256KB / 总计 20MB / 500 条目，防 zip 炸弹；
- 解码深度 2，防止递归膨胀；
- `.repohealth.json` 规则长度/数量上限，编译失败跳过；
- gitleaks 的 `generic-api-key` 规则默认丢弃（真实语料误报高，已有对比数据支撑）。
