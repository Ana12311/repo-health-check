"""仓库类型识别：software / docs / data / empty / unknown。

类型决定评分 rubric——纯文档仓库不该因为「没有启动脚本」被判差。
"""
from __future__ import annotations


def classify_repo(signals: dict) -> dict:
    source_files = signals.get("source_files", 0)
    manifests = signals.get("manifests", 0)
    doc_ratio = signals.get("doc_ratio", 0.0)
    doc_files = signals.get("doc_files", 0)
    notebooks = signals.get("notebooks", 0)
    data_files = signals.get("data_files", 0)
    total_files = signals.get("total_files", 0)

    evidence: list[str] = []

    if total_files < 3:
        return {
            "type": "empty",
            "confidence": "high",
            "evidence": [f"仓库文件极少（{total_files} 个），无法评估接入就绪度"],
        }

    # 文档绝对主导：即使带几个整理脚本，也按文档仓库评
    if doc_ratio >= 0.7 and doc_files >= 5 and source_files < 30:
        evidence.append(f"文档占比 {doc_ratio:.0%}（{doc_files} 个文档文件）")
        evidence.append(f"源码文件仅 {source_files} 个")
        return {"type": "docs", "confidence": "high", "evidence": evidence}

    if source_files >= 10 or (source_files >= 3 and manifests >= 1):
        evidence.append(f"源码文件 {source_files} 个")
        evidence.append(f"依赖清单 {manifests} 个")
        return {
            "type": "software",
            "confidence": "high" if manifests else "medium",
            "evidence": evidence,
        }

    # 数据仓库：notebook/数据文件要多到足以定义仓库性质
    if (notebooks >= 3 and notebooks * 2 >= source_files) or (
        data_files >= 5 and data_files >= source_files
    ):
        evidence.append(f"notebook {notebooks} 个，数据文件 {data_files} 个")
        return {"type": "data", "confidence": "high", "evidence": evidence}

    if doc_files >= 3:
        evidence.append(f"文档文件 {doc_files} 个，源文件 {source_files} 个")
        return {"type": "docs", "confidence": "low", "evidence": evidence}

    if source_files >= 3:
        evidence.append(f"源码文件 {source_files} 个，但无包管理清单")
        return {"type": "software", "confidence": "low", "evidence": evidence}

    return {
        "type": "unknown",
        "confidence": "low",
        "evidence": [
            f"文件构成不典型：源码 {source_files}，文档 {doc_files}，"
            f"清单 {manifests}，数据 {data_files}"
        ],
    }
