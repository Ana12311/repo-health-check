"""从评测结果 JSON 生成 README 用数据图（PNG），深色仪表盘风格。

数据来源：eval/results.json、eval/recall_results.json、eval/compare_results.json。
人工抽检数据来自 eval/REPORT.md。

用法：python eval/plot_report.py
"""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))

BG = "#0f172a"
TITLE = "#f1f5f9"
SUBTITLE = "#94a3b8"
LABEL = "#cbd5e1"
FOOT = "#64748b"
GRID = "#1e293b"

PALETTE = ["#22d3ee", "#34d399", "#a78bfa", "#fbbf24", "#f472b6", "#60a5fa"]


def _load(name: str) -> dict:
    with open(os.path.join(EVAL_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _score_median_by_category(results: dict) -> dict[str, float]:
    dist = results["summary"]["score_distribution"]["by_category"]
    order = ["python", "node", "java", "go", "rust", "docs", "data", "security", "windows"]
    return {k: dist[k]["median"] for k in order if k in dist}


def _header(fig, title: str, subtitle: str):
    """居中标题 + 副标题（图级坐标，不压图）。"""
    fig.text(0.5, 0.93, title, ha="center", va="top",
             fontsize=17, fontweight="bold", color=TITLE)
    fig.text(0.5, 0.875, subtitle, ha="center", va="top",
             fontsize=10, color=SUBTITLE)


def _footer(fig, note: str):
    fig.text(0.02, 0.02, note, ha="left", va="bottom",
             fontsize=8, color=FOOT)


def chart_accuracy(r50: dict) -> None:
    type_acc = r50["summary"]["type_accuracy"]["accuracy"] * 100
    win_acc = r50["summary"]["windows_accuracy"]["accuracy"] * 100

    labels = ["仓库类型识别", "Windows 脚本检出", "注入缺陷召回",
              "高危路径抽检", "Agent 复核", "中危路径抽检"]
    values = [type_acc, win_acc, 100.0, 100.0, 95.0, 72.0]
    n = len(labels)

    fig, ax = plt.subplots(figsize=(8.6, 5.2))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    fig.subplots_adjust(left=0.24, right=0.94, top=0.80, bottom=0.08)

    ys = list(range(n))[::-1]
    bar_h = 0.52
    for y, v, c in zip(ys, values, PALETTE):
        box = FancyBboxPatch(
            (0, y - bar_h / 2), v, bar_h,
            boxstyle="round,pad=0,rounding_size=0.16",
            linewidth=0, facecolor=c, alpha=0.95,
        )
        ax.add_patch(box)
        ax.text(v + 2, y, f"{v:.0f}%", va="center", ha="left",
                fontsize=11.5, fontweight="bold", color=c)

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=11.5, color=LABEL)
    ax.set_xlim(0, 118)
    ax.set_ylim(-0.72, n - 0.28)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0)
    for s in ax.spines.values():
        s.set_visible(False)

    _header(fig, "评测核心指标", "Accuracy & recall — 50 public repos")
    _footer(fig, "数据来源 eval/results.json · eval/recall_results.json · 2026-11")

    plt.savefig(os.path.join(EVAL_DIR, "accuracy.png"), dpi=160,
                facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def chart_score(r50: dict) -> None:
    medians = _score_median_by_category(r50)
    names = {
        "python": "Python", "node": "Node.js", "java": "Java", "go": "Go",
        "rust": "Rust", "docs": "文档", "data": "数据",
        "security": "安全", "windows": "Windows",
    }
    cats = [names[k] for k in medians]
    vals = list(medians.values())

    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    fig.subplots_adjust(left=0.06, right=0.94, top=0.80, bottom=0.14)

    xs = range(len(cats))
    w = 0.52
    for x, v, c in zip(xs, vals, [PALETTE[i % len(PALETTE)] for i in range(len(cats))]):
        box = FancyBboxPatch(
            (x - w / 2, 0), w, v,
            boxstyle="round,pad=0,rounding_size=0.12",
            linewidth=0, facecolor=c, alpha=0.95,
        )
        ax.add_patch(box)
        ax.text(x, v + 2, f"{v:.0f}", ha="center", va="bottom",
                fontsize=10.5, fontweight="bold", color=TITLE)

    ax.axhline(80, color="#fbbf24", linestyle="--", linewidth=1.2, alpha=0.8, zorder=2)
    ax.text(len(cats) - 0.5, 81.5, "优 · 80 分", fontsize=9, color="#fbbf24", ha="right")

    ax.set_xlim(-0.6, len(cats) - 0.4)
    ax.set_ylim(0, 110)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(cats, fontsize=10.5, color=LABEL, rotation=20)
    ax.set_yticks([])
    ax.tick_params(axis="x", length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    _header(fig, "各类型仓库评分中位数", "Median readiness score by repository type")
    _footer(fig, "数据来源 eval/results.json（50 个真实公开仓库）· 2026-11")

    plt.savefig(os.path.join(EVAL_DIR, "score_distribution.png"), dpi=160,
                facecolor=BG, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    r50 = _load("results.json")
    chart_accuracy(r50)
    chart_score(r50)
    print("已生成 accuracy.png score_distribution.png")


if __name__ == "__main__":
    main()
