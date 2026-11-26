# 与 gitleaks 对比（precision / recall）

生成时间：2026-11-26
工具版本：gitleaks 8.30.1（官方 Windows x64 发行版，默认配置）
脚本：`eval/compare_gitleaks.py`；原始数据：`eval/compare_results.json`（本地运行生成，未入库，避免收录被扫仓库的密钥）

## 一、注入召回（36 次 = 12 类密钥 × 3 个真实仓库副本）

| 工具 | 检出 | 召回率 |
|---|---|---|
| 本项目（规则层） | **36 / 36** | **100%** |
| gitleaks 8.30.1 | 33 / 36 | 91.7% |

gitleaks 漏检 3 次全部是 **Anthropic key（`sk-ant-`）** —— 8.30.1 默认规则库没有该规则。本项目覆盖。

> 注：第一轮对比发现我们自己的 AWS 规则过宽（`[0-9A-Z]`），把不符合 AWS base32 字符集的 token 也算命中；
> 已对齐 gitleaks 的 `AKIA[A-Z2-7]{16}` 并修正测试夹具。修正后双方在 AWS 上一致。

## 二、真实仓库噪音量（8 个未注入的仓库）

| 仓库 | gitleaks 条数 | 本项目「真实」 | 本项目「文档示例」 | 同 file:line 重合 |
|---|---|---|---|---|
| pallets/flask | 6 | 0 | 6 | 5 |
| tiangolo/fastapi | 21 | 0 | 10 | 8 |
| gin-gonic/gin | 4 | 0 | 0 | 0 |
| BurntSushi/ripgrep | 0 | 0 | 0 | 0 |
| axios/axios | 1 | 0 | 1 | 1 |
| django/django | 9 | 0 | 3 | 2 |
| gitleaks/gitleaks | 2 | 0 | 96 | 0 |
| trufflesecurity/trufflehog | **1432** | 0 | 433 | 378 |
| **合计** | **1475** | **0** | **549** | — |

## 三、抽样人工判断（gitleaks 命中）

| 仓库 | gitleaks 命中 | 实际性质 |
|---|---|---|
| gin | `testdata/certificate/key.pem` 私钥 | **真阳性**（测试证书私钥，仍属私钥入库）；本项目按设计跳过 `testdata/`，漏掉 |
| gin | `generic-api-key`：`float32-slice`、`float64-slice`、base64 nonce | **误报** |
| django | `generic-api-key`：`Asia/Singapore`、`Europe/Helsinki`、`since_days=365`、`raise_exception=True` | **误报** |
| django | `tests/csrf_tests/tests.py` 的 MASKED_TEST_SECRET | 测试夹具 |
| fastapi | 21 条 JWT，全部来自 `docs/*/tutorial/oauth2-jwt.md` | 文档示例（本项目归为文档示例，不计分） |

结论：gitleaks 的 provider 规则精确，但 `generic-api-key` 规则在真实代码上误报明显（gin 3/4、django 约 6/9）；本项目用「值熵 + 常量形状 + 文档目录」过滤掉这类噪音。反过来，gitleaks 会扫 `testdata/`，抓到了我们按设计跳过的真实私钥。

## 四、能力矩阵

| 能力 | 本项目 | gitleaks 8.30.1 |
|---|---|---|
| Provider 密钥规则数 | 13 条（含 Anthropic/HF/npm） | ~200 条（社区维护） |
| Generic 明文凭据 | 有，但强制熵 + 形状过滤 | 有，误报较多 |
| 文档/测试上下文识别 | **有**（分类 + 降级） | 无 |
| 仓库类型感知评分 | **有** | 无（不是评分工具） |
| Git 历史扫描 | 无（只扫 HEAD） | **有** |
| 压缩包/编码递归解码 | 无 | **有**（decode depth 5） |
| 自定义规则/基线 | 无 | **有**（`.gitleaks.toml`、baseline） |
| testdata 目录 | 跳过（漏检真实测试私钥） | 扫描 |
| 维护成本 | 自维护 | 社区维护 |

## 五、结论

1. **召回**：在 12 类常见 provider 格式上，本项目 100% vs gitleaks 91.7%；但 gitleaks 规则库广度远超我们，冷门 provider 我们必然漏。
2. **精确**：本项目通过上下文分类显著降低 generic 误报（8 个仓库 0 条「真实」误报，gitleaks 约 10+ 条），代价是跳过 `testdata/` 会漏真实私钥。
3. **定位不同**：gitleaks 是安全审计工具，追求覆盖；本项目是接入体检工具，追求「分数不被噪音污染」。两者不能互相替代。
4. **工程建议**：接入体检场景可把 gitleaks 作为第二引擎并行跑，取其 provider 命中、丢弃其 generic 规则，再用本项目上下文分类降级——召回取并集，精确靠分类。

## 局限

- 只对比了 8 个仓库 + 36 次注入，统计意义有限。
- 未对比 Git 历史（双方都只扫工作区）。
- gitleaks 默认配置；调参后（关闭 generic 规则）误报会大幅下降，届时差距主要在上下文分类与评分。
