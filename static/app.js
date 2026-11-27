const form = document.getElementById("analyze-form");
const input = document.getElementById("repo-url");
const btn = document.getElementById("submit-btn");
const errorBox = document.getElementById("error-box");
const loading = document.getElementById("loading");
const result = document.getElementById("result");
const apiKeyInput = document.getElementById("api-key");
const saveKeyBtn = document.getElementById("save-key");
const keyStatus = document.getElementById("key-status");

// 启动时读取当前 key 配置状态
(async function loadKeyStatus() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    renderKeyStatus(d);
  } catch (e) { /* 忽略 */ }
})();

function renderKeyStatus(d) {
  if (d.runtime) {
    keyStatus.textContent = "已配置（本次填的 key）✓";
    keyStatus.className = "key-status on";
  } else if (d.env) {
    keyStatus.textContent = "已配置（.env / 环境变量）";
    keyStatus.className = "key-status on";
  } else {
    keyStatus.textContent = "未配置";
    keyStatus.className = "key-status off";
  }
}

saveKeyBtn.addEventListener("click", async () => {
  const key = apiKeyInput.value.trim();
  saveKeyBtn.disabled = true;
  try {
    const r = await fetch("/api/set-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    const d = await r.json();
    renderKeyStatus({ runtime: d.key_configured && key !== "", env: false });
    if (key === "") {
      // 清空后回退到 .env 状态，重新查一次
      const r2 = await fetch("/api/status");
      renderKeyStatus(await r2.json());
    }
  } catch (e) {
    keyStatus.textContent = "保存失败：" + e.message;
    keyStatus.className = "key-status off";
  } finally {
    saveKeyBtn.disabled = false;
  }
});

const SEV_TEXT = { high: "高危", medium: "中危", low: "低危" };
const CONF_TEXT = { high: "确证", medium: "疑似", low: "待确认" };
const AGENT_TEXT = { confirmed: "AI 已确认", uncertain: "AI 存疑", false_positive: "AI 判误报" };

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = input.value.trim();
  if (!url) return;

  errorBox.classList.add("hidden");
  result.classList.add("hidden");
  loading.classList.remove("hidden");
  btn.disabled = true;

  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: url, api_key: apiKeyInput.value.trim() }),
    });
    const data = await resp.json();
    if (!data.ok) {
      showError(data.error || "分析失败");
      return;
    }
    render(data);
    result.classList.remove("hidden");
  } catch (err) {
    showError("请求失败：" + err.message);
  } finally {
    loading.classList.add("hidden");
    btn.disabled = false;
  }
});

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.remove("hidden");
}

function render(data) {
  const s = data.score;
  const scan = data.scan;
  const arch = s.archetype || {};
  const sum = s.summary || {};

  // 仓库信息 + 类型
  const typeLabel = arch.label || arch.type || "未分类";
  const typeConf = arch.confidence === "high" ? "" : "（置信度低）";
  document.getElementById("meta").innerHTML =
    `<b>${escapeHtml(data.owner)}/${escapeHtml(data.repo)}</b>` +
    ` <span class="type-badge">${escapeHtml(typeLabel)}${typeConf}</span>` +
    (data.commit_sha ? ` · commit <code>${escapeHtml(data.commit_sha.slice(0, 8))}</code>` : "");

  // 分析统计
  const stats = document.getElementById("stats");
  stats.textContent =
    `扫描 ${sum.files_scanned ?? 0} 个文件` +
    (sum.skipped_large ? `（跳过 ${sum.skipped_large} 个大文件）` : "") +
    (sum.files_total ? ` · 仓库共 ${sum.files_total} 个文件` : "") +
    (data.elapsed_ms ? ` · 耗时 ${(data.elapsed_ms / 1000).toFixed(1)}s` : "");

  // 评分环
  const ring = document.getElementById("ring-fg");
  const C = 2 * Math.PI * 52;
  ring.style.strokeDasharray = C;
  if (s.score === null || s.score === undefined) {
    ring.style.strokeDashoffset = C;
    document.getElementById("score-num").textContent = "N/A";
    document.getElementById("score-grade").textContent = "无法评估";
  } else {
    ring.style.strokeDashoffset = C * (1 - s.score / 100);
    ring.style.stroke = colorOf(s.score);
    document.getElementById("score-num").textContent = s.score;
    document.getElementById("score-grade").textContent = s.grade;
  }

  // 概览 chips
  const chips = document.getElementById("summary-chips");
  chips.innerHTML = "";
  chips.appendChild(chip(`密钥风险 ${sum.secrets ?? 0}`, sum.secrets ? "sev-high" : "ok"));
  chips.appendChild(chip(`硬编码路径 ${sum.hardcoded_paths ?? 0}`, sum.hardcoded_paths ? "sev-medium" : "ok"));
  chips.appendChild(chip(`Windows 脚本 ${sum.windows_scripts ?? 0}`, sum.windows_scripts ? "ok" : "sev-low"));
  chips.appendChild(chip(`依赖清单 ${sum.dep_manifests ?? 0}`, sum.dep_manifests ? "ok" : "sev-medium"));
  if (sum.doc_findings) chips.appendChild(chip(`文档示例 ${sum.doc_findings}（不计分）`, "sev-low"));
  if (sum.history) chips.appendChild(chip(`历史泄露 ${sum.history}（不计分）`, "sev-medium"));
  if (sum.ignored) chips.appendChild(chip(`基线忽略 ${sum.ignored}`, "sev-low"));
  if (sum.agent_reviewed) {
    chips.appendChild(chip(
      `AI 复核 ${sum.agent_reviewed} 条 · 判误报 ${sum.agent_downgraded || 0}`,
      "sev-low"));
  }

  // 类型识别依据
  const evidence = document.getElementById("type-evidence");
  if (arch.evidence && arch.evidence.length) {
    evidence.textContent = "类型判断依据：" + arch.evidence.join("；");
    evidence.classList.remove("hidden");
  } else {
    evidence.classList.add("hidden");
  }

  // 分项得分
  const comps = document.getElementById("components");
  comps.innerHTML = "";
  const entries = Object.entries(s.components || {});
  if (!entries.length) {
    comps.innerHTML = '<div class="empty">该仓库无法评分，仅提供事实清单。</div>';
  }
  for (const [, c] of entries) {
    if (!c.applicable) continue;
    const pct = c.max ? Math.round((c.score / c.max) * 100) : 0;
    const div = document.createElement("div");
    div.className = "comp";
    div.innerHTML = `
      <div class="comp-head">
        <span class="comp-name">${escapeHtml(c.name)}</span>
        <span class="comp-val">${c.score} / ${c.max}</span>
      </div>
      <div class="bar"><div class="bar-fill" style="width:${pct}%;background:${colorOf(pct)}"></div></div>
      ${c.notes && c.notes.length ? `<ul class="comp-notes">${c.notes.map((n) => "<li>" + escapeHtml(n) + "</li>").join("")}</ul>` : ""}
    `;
    comps.appendChild(div);
  }

  // 风险清单
  const risks = document.getElementById("risks");
  risks.innerHTML = "";
  if (!s.risks.length) {
    risks.innerHTML = '<div class="empty">未发现明显风险 ✅</div>';
  } else {
    for (const r of s.risks) {
      const div = document.createElement("div");
      div.className = "risk sev-" + r.severity;
      const loc = r.file ? `<span class="loc">${escapeHtml(r.file)}${r.line ? ":" + r.line : ""}</span> ` : "";
      const conf = r.confidence ? `<span class="conf">${CONF_TEXT[r.confidence] || r.confidence}</span>` : "";
      const agent = r.agent ? `<span class="conf agent">${AGENT_TEXT[r.agent.verdict] || "AI 复核"}</span>` : "";
      const reasons = [r.reason, r.agent && r.agent.reason ? "AI：" + r.agent.reason : ""]
        .filter(Boolean).join(" · ");
      const reason = reasons ? `<div class="risk-reason">${escapeHtml(reasons)}</div>` : "";
      div.innerHTML =
        `<span class="tag sev-${r.severity}">${SEV_TEXT[r.severity] || r.severity}</span>${conf}${agent}` +
        `${loc}<span class="detail">${escapeHtml(r.detail)}</span>${reason}`;
      risks.appendChild(div);
    }
  }

  // AI 建议
  const aiSection = document.getElementById("ai-section");
  if (data.ai && ((data.ai.advice && data.ai.advice.length) || data.ai.verdict)) {
    aiSection.classList.remove("hidden");
    document.getElementById("ai-model").textContent = data.ai.model ? "（" + data.ai.model + "）" : "";
    const verdict = document.getElementById("ai-verdict");
    verdict.textContent = data.ai.verdict ? "结论：" + data.ai.verdict : "";
    verdict.classList.toggle("hidden", !data.ai.verdict);
    document.getElementById("ai-reason").textContent = data.ai.reason || "";
    document.getElementById("ai-advice").innerHTML = (data.ai.advice || [])
      .map((a) => "<li>" + escapeHtml(a) + "</li>")
      .join("");
  } else {
    aiSection.classList.add("hidden");
  }
}

function chip(text, cls) {
  const d = document.createElement("span");
  d.className = "chip " + cls;
  d.textContent = text;
  return d;
}

function colorOf(score) {
  if (score >= 80) return "var(--good)";
  if (score >= 60) return "var(--accent)";
  if (score >= 40) return "var(--mid)";
  return "var(--bad)";
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
