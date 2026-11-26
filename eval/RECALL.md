# 召回率测试（注入缺陷）

生成时间：2026-11-26
脚本：`eval/recall_test.py`；原始数据：`eval/recall_results.json`（本地运行生成，未入库）

## 方法

选 3 个真实仓库副本（`pallets/flask`、`Ana12311/TaskFlow`、`elementalsouls/Claude-BugHunter`），
向其中注入 **21 类已知缺陷**，共 **63 次注入**（每类 × 3 仓库），跑完整扫描管线，统计检出率。

注入内容与预期：

| 类别 | 缺陷 | 预期严重度/置信度 |
|---|---|---|
| 密钥 × 12 | OpenAI `sk-proj-`、GitHub `ghp_`、AWS `AKIA`、腾讯 `AKID`、Slack `xoxb-`、GitLab `glpat-`、Google `AIza`、HuggingFace `hf_`、npm `npm_`、Anthropic `sk-ant-`、完整 RSA PEM、明文 `DB_PASSWORD` | high（明文密码为 medium） |
| `.env` 入库 | 根目录 `.env` | high |
| 路径 × 4 | `C:\Users\alice\...`、`/home/alice/...`、`D:\build\...`（在 `.ps1` 中）、`/opt/appdata/...` | medium（脚本内为 high） |
| Windows 脚本 × 2 | `scripts/run.bat`、`scripts/setup.ps1` | 检出即可 |
| 文档/测试诱饵 × 2 | `docs/guide.md` 中的示例 key、`tests/test_config.py` 中的假 AWS key | 应检出，但归类为文档示例、不计分 |

## 结果

| 指标 | 结果 |
|---|---|
| 总注入次数 | 63 |
| 总检出 | **63 / 63 = 100%** |
| 计分缺陷检出 | **51 / 51 = 100%** |
| Agent 复核后误降级 | **0** |

- 用真实 DeepSeek 跑 Agent 复核（3 个仓库各一次调用），没有任何注入缺陷被判为误报。
- 文档/测试诱饵：全部被检出，且被正确归为「文档示例」，不计入评分（未导致分数下降）。

## 测试中发现并修复的问题

**无扩展名 PEM 文件漏检**：注入的 `deploy/keys/id_deploy` 第一轮未检出。原因：文件遍历只认已知扩展名和白名单文件名（`id_rsa` 等），无扩展名的私钥文件被当二进制跳过。

修复：无扩展名文件读前 64 字节，含 `-----BEGIN` 则纳入扫描。复测 100%，并加了回归测试 `test_extensionless_pem_file_detected`。

## 局限（重要）

本测试测的是**「已知格式的检出能力」**，不是完整的召回率：

1. 注入的都是教科书式格式，真实泄露可能被拆分拼接、Base64 编码、放在二进制/大文件里。
2. 未覆盖：>256KB 文件、>64KB notebook、`testdata/` 目录（按设计跳过，可能藏真密钥）。
3. 注入位置都是「正常代码目录」，没有测试「密钥出现在奇怪位置」的情况。
4. 样本只有 3 个仓库、21 类格式，不能推导所有语言/框架下的召回率。
5. 文档诱饵判定依赖目录规则（`docs/`、`tests/`），若泄露发生在非标准命名的目录，会按真实风险计分（更保守，但可能误报）。

结论：**格式明确的缺陷，规则层召回可靠**；语义型、编码型、非常规位置的泄露，当前无法保证。
