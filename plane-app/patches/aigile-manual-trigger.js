(function () {
  const PATCH_VERSION = "20260605-review-disclosure-1";
  if (window.__aigilePlaneActionsVersion === PATCH_VERSION) return;
  if (typeof window.__aigilePlaneActionsCleanup === "function") {
    window.__aigilePlaneActionsCleanup();
  }
  window.__aigilePlaneActionsVersion = PATCH_VERSION;
  window.__aigilePlaneActionsInstalled = true;

  const API_BASE = "http://localhost:8091";
  const KEY_PATTERN = /\b[A-Z][A-Z0-9_]{1,11}-\d+\b/;
  const RESTORE_REVIEW_STATE = {
    issueKey: null,
    reviewId: null,
    inFlight: null,
  };

  const ACTION_ROW_TEXTS = [
    "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u0434\u044d\u043b\u0435\u043c\u0435\u043d\u0442",
    "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0441\u0432\u044f\u0437\u044c",
    "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0441\u0441\u044b\u043b\u043a\u0443",
    "\u041f\u0440\u0438\u043a\u0440\u0435\u043f\u0438\u0442\u044c",
    "Add sub",
    "Add relation",
    "Add link",
    "Attach",
  ];

  const ACTIONS = [
    {
      id: "ai-analysis",
      elementId: "aigile-ai-trigger-button",
      ariaLabel: "Run AI review for this Plane issue",
      labels: {
        idle: "AI \u0430\u043d\u0430\u043b\u0438\u0437",
        loading: "AI \u0430\u043d\u0430\u043b\u0438\u0437...",
        success: "AI \u0433\u043e\u0442\u043e\u0432",
        error: "AI \u043e\u0448\u0438\u0431\u043a\u0430",
      },
      run: runAiReview,
    },
  ];

  function uniqueIssueKeys(text) {
    return Array.from(new Set(String(text || "").match(KEY_PATTERN) || []));
  }

  function findIssueKeyNearActionRow(actionRow) {
    if (!actionRow) return null;
    const rowRect = actionRow.getBoundingClientRect();
    const candidates = [];
    const elements = Array.from(document.querySelectorAll("div, span, p, a, button, h1, h2, h3"));

    for (const element of elements) {
      const text = (element.innerText || element.textContent || "").trim();
      const keys = uniqueIssueKeys(text);
      if (keys.length !== 1) continue;

      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) continue;

      const inSameDetailPane = rect.left >= rowRect.left - 80 && rect.right <= window.innerWidth + 20;
      const aboveActionRow = rect.bottom <= rowRect.top + 8;
      const closeToActionRow = rowRect.top - rect.bottom < 340;
      if (!inSameDetailPane || !aboveActionRow || !closeToActionRow) continue;

      candidates.push({
        key: keys[0],
        distance: Math.abs(rowRect.top - rect.bottom),
        top: rect.top,
      });
    }

    candidates.sort((a, b) => a.distance - b.distance || b.top - a.top);
    return candidates.length ? candidates[0].key : null;
  }

  function findIssueKey(preferredNode) {
    const actionRow = preferredNode
      ? preferredNode.closest("[data-aigile-action-row='true']") || findActionRow()
      : findActionRow();
    const nearbyKey = findIssueKeyNearActionRow(actionRow);
    if (nearbyKey) return nearbyKey;

    const pathMatch = window.location.pathname.match(KEY_PATTERN);
    if (pathMatch) return pathMatch[0];

    const titleMatch = (document.title || "").match(KEY_PATTERN);
    if (titleMatch) return titleMatch[0];

    const visibleText = document.body ? document.body.innerText.slice(0, 20000) : "";
    const visibleMatch = visibleText.match(KEY_PATTERN);
    return visibleMatch ? visibleMatch[0] : null;
  }

  function findActionRow() {
    const elements = Array.from(document.querySelectorAll("button, [role='button'], a, div"));
    for (const element of elements) {
      const text = (element.innerText || element.textContent || "").trim();
      if (!text || !ACTION_ROW_TEXTS.some((label) => text.includes(label))) continue;

      let row = element.parentElement;
      while (row && row !== document.body) {
        const rect = row.getBoundingClientRect();
        const actionMatches = ACTION_ROW_TEXTS.filter((label) => (row.innerText || "").includes(label)).length;
        const interactiveCount = row.querySelectorAll("button, [role='button'], a").length;
        const compactActionRow = rect.height > 0 && rect.height <= 96 && rect.width >= 240;
        if (actionMatches >= 2 && interactiveCount >= 3 && compactActionRow) {
          row.dataset.aigileActionRow = "true";
          return row;
        }
        row = row.parentElement;
      }
    }
    return null;
  }

  function findNativeActionButton() {
    const labels = ["\u041f\u0440\u0438\u043a\u0440\u0435\u043f\u0438\u0442\u044c", "Attach"];
    const controls = Array.from(document.querySelectorAll("button, [role='button'], a"));
    const candidates = [];
    for (const control of controls) {
      if (control.id === "aigile-ai-trigger-button") continue;
      const text = (control.innerText || control.textContent || "").trim();
      if (!labels.some((label) => text.includes(label))) continue;
      const rect = control.getBoundingClientRect();
      if (!rect.width || !rect.height) continue;
      let row = control.parentElement;
      while (row && row !== document.body) {
        const rowRect = row.getBoundingClientRect();
        const rowText = row.innerText || "";
        const rowMatches = ACTION_ROW_TEXTS.filter((label) => rowText.includes(label)).length;
        const interactiveCount = row.querySelectorAll("button, [role='button'], a").length;
        const compactActionRow = rowRect.height > 0 && rowRect.height <= 96 && rowRect.width >= 240;
        if (rowMatches >= 2 && interactiveCount >= 3 && compactActionRow) {
          row.dataset.aigileActionRow = "true";
          candidates.push({ control, row, top: rect.top, left: rect.left, width: rowRect.width });
          break;
        }
        row = row.parentElement;
      }
    }
    candidates.sort((a, b) => b.top - a.top || a.width - b.width || b.left - a.left);
    return candidates.length ? candidates[0] : null;
  }

  function isIssueActionSurface() {
    return Boolean(findIssueKey()) && Boolean(findActionRow());
  }

  function applyButtonStyle(button) {
    button.style.position = "static";
    button.style.zIndex = "auto";
    button.style.display = "inline-flex";
    button.style.alignItems = "center";
    button.style.justifyContent = "center";
    button.style.gap = "6px";
    button.style.height = "28px";
    button.style.minHeight = "28px";
    button.style.maxHeight = "28px";
    button.style.padding = "0 10px";
    button.style.border = "1px solid rgb(47, 51, 56)";
    button.style.background = "rgb(24, 26, 28)";
    button.style.color = "rgb(205, 209, 214)";
    button.style.borderRadius = "6px";
    button.style.fontSize = "13px";
    button.style.lineHeight = "18px";
    button.style.fontWeight = "600";
    button.style.cursor = "pointer";
    button.style.boxShadow = "none";
    button.style.whiteSpace = "nowrap";
    button.style.margin = "0";
    button.style.fontFamily = "inherit";
    button.style.opacity = "1";
  }

  function setButtonState(button, action, state) {
    button.dataset.state = state;
    button.textContent = action.labels[state];
    button.disabled = state === "loading";
    applyButtonStyle(button);

    if (state === "success") {
      button.style.borderColor = "rgba(34, 197, 94, 0.55)";
      button.style.color = "rgb(134, 239, 172)";
      button.style.background = "rgba(22, 101, 52, 0.2)";
    } else if (state === "error") {
      button.style.borderColor = "rgba(248, 113, 113, 0.55)";
      button.style.color = "rgb(252, 165, 165)";
      button.style.background = "rgba(127, 29, 29, 0.22)";
    } else if (state === "loading") {
      button.style.opacity = "0.72";
      button.style.cursor = "default";
    }
  }

  function showToast(text, kind) {
    let toast = document.getElementById("aigile-plane-action-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "aigile-plane-action-toast";
      toast.style.position = "fixed";
      toast.style.right = "20px";
      toast.style.bottom = "20px";
      toast.style.zIndex = "2147483647";
      toast.style.maxWidth = "360px";
      toast.style.padding = "10px 12px";
      toast.style.borderRadius = "8px";
      toast.style.fontSize = "13px";
      toast.style.lineHeight = "18px";
      toast.style.boxShadow = "0 12px 30px rgba(0, 0, 0, 0.32)";
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.style.background = kind === "error" ? "rgb(69, 26, 26)" : "rgb(20, 83, 45)";
    toast.style.color = kind === "error" ? "rgb(254, 202, 202)" : "rgb(187, 247, 208)";
    toast.style.border = kind === "error" ? "1px solid rgb(127, 29, 29)" : "1px solid rgb(22, 101, 52)";
    clearTimeout(window.__aigilePlaneActionToastTimer);
    window.__aigilePlaneActionToastTimer = setTimeout(() => toast.remove(), 6000);
  }

  async function runAiReview(issueKey) {
    const response = await fetch(`${API_BASE}/api/review-task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_slug: "aigile", issue_key: issueKey }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.payload = data;
      throw error;
    }
    return data;
  }

  async function fetchLatestReview(issueKey) {
    const response = await fetch(`${API_BASE}/api/review-history?issue_key=${encodeURIComponent(issueKey)}&limit=1`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const reviews = Array.isArray(data.reviews) ? data.reviews : [];
    return reviews.length ? reviews[reviews.length - 1] : null;
  }

  async function runApplySuggestion(review, agent, agentIndex, findingIndex) {
    const response = await fetch(`${API_BASE}/api/apply-review-suggestion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_slug: "aigile",
        issue_key: review.issue_key || findIssueKey(),
        review_id: review.review_id,
        agent_name: agent.agent_name,
        agent_index: agentIndex,
        finding_index: findingIndex,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  async function runStartTaskChat(review) {
    const response = await fetch(`${API_BASE}/api/start-task-chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_slug: "aigile",
        issue_key: review.issue_key || findIssueKey(),
        review_id: review.review_id,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  async function runApplyReviewSummary(review, summary) {
    const response = await fetch(`${API_BASE}/api/apply-review-summary-comment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        workspace_slug: "aigile",
        issue_key: review.issue_key || findIssueKey(),
        review_id: review.review_id,
        summary,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  function statusColor(status) {
    if (status === "green") return "rgb(34, 197, 94)";
    if (status === "red") return "rgb(248, 113, 113)";
    return "rgb(234, 179, 8)";
  }

  function findIssueDetailSurface() {
    const actionRow = findActionRow();
    if (!actionRow) return null;
    let node = actionRow.parentElement;
    while (node && node !== document.body) {
      const text = node.innerText || "";
      const rect = node.getBoundingClientRect();
      if (rect.width >= 420 && (text.includes("\u0421\u0432\u043e\u0439\u0441\u0442\u0432\u0430") || text.includes("Properties"))) {
        return node;
      }
      node = node.parentElement;
    }
    return actionRow.parentElement;
  }

  function createReviewPanel() {
    let panel = document.getElementById("aigile-ai-review-panel");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "aigile-ai-review-panel";
      panel.style.margin = "18px 0";
      panel.style.padding = "12px";
      panel.style.border = "1px solid rgb(47, 51, 56)";
      panel.style.borderRadius = "8px";
      panel.style.background = "rgb(18, 20, 22)";
      panel.style.color = "rgb(205, 209, 214)";
      panel.style.fontFamily = "inherit";
      panel.style.fontSize = "13px";
      panel.style.lineHeight = "18px";
    }
    return panel;
  }

  function applySecondaryButtonStyle(button) {
    button.style.marginTop = "8px";
    button.style.height = "28px";
    button.style.padding = "0 10px";
    button.style.border = "1px solid rgb(47, 51, 56)";
    button.style.borderRadius = "6px";
    button.style.background = "rgb(24, 26, 28)";
    button.style.color = "rgb(205, 209, 214)";
    button.style.fontSize = "12px";
    button.style.fontWeight = "700";
    button.style.cursor = "pointer";
  }

  function applyInlineButtonStyle(button) {
    button.style.height = "28px";
    button.style.padding = "0 10px";
    button.style.border = "1px solid rgb(47, 51, 56)";
    button.style.borderRadius = "6px";
    button.style.background = "rgb(24, 26, 28)";
    button.style.color = "rgb(205, 209, 214)";
    button.style.fontSize = "12px";
    button.style.fontWeight = "700";
    button.style.cursor = "pointer";
    button.style.whiteSpace = "nowrap";
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function severityRank(severity) {
    const normalized = String(severity || "medium").toLowerCase();
    if (normalized === "critical") return 4;
    if (normalized === "high") return 3;
    if (normalized === "medium") return 2;
    if (normalized === "low") return 1;
    return 2;
  }

  function normalizeIssueTitle(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("acceptance") || text.includes("criteria")) return "acceptance criteria";
    if (text.includes("business") || text.includes("outcome")) return "business outcome";
    if (text.includes("scope")) return "scope boundaries";
    if (text.includes("depend")) return "dependencies";
    if (text.includes("user") || text.includes("persona")) return "target users";
    if (text.includes("security")) return "security review";
    if (text.includes("test") || text.includes("qa")) return "testability";
    return text.replace(/[^a-z0-9а-яё ]/gi, " ").replace(/\s+/g, " ").trim() || "review issue";
  }

  function friendlyIssueTitle(key) {
    const labels = {
      "acceptance criteria": "Missing acceptance criteria",
      "business outcome": "Missing business outcome",
      "scope boundaries": "Unclear scope boundaries",
      dependencies: "Missing dependencies",
      "target users": "Unclear target users",
      "security review": "Security review needed",
      testability: "Testability is unclear",
    };
    return labels[key] || key.replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function groupReviewIssues(agents) {
    const groups = new Map();
    for (const agent of agents) {
      const findings = Array.isArray(agent.findings) ? agent.findings : [];
      for (const finding of findings) {
        const key = normalizeIssueTitle(`${finding.title || ""} ${finding.description || ""}`);
        const existing = groups.get(key) || {
          id: key,
          title: friendlyIssueTitle(key),
          severity: "low",
          impactedAgents: [],
          descriptions: [],
          recommendations: [],
        };
        if (severityRank(finding.severity) > severityRank(existing.severity)) {
          existing.severity = String(finding.severity || "medium").toLowerCase();
        }
        if (agent.agent_name && !existing.impactedAgents.includes(agent.agent_name)) {
          existing.impactedAgents.push(agent.agent_name);
        }
        if (finding.description) existing.descriptions.push(finding.description);
        if (finding.recommendation) existing.recommendations.push(finding.recommendation);
        groups.set(key, existing);
      }
    }
    return Array.from(groups.values()).sort((a, b) => severityRank(b.severity) - severityRank(a.severity));
  }

  function calculateReadinessScore(groupedIssues) {
    const penalties = { low: 5, medium: 10, high: 15, critical: 25 };
    const score = groupedIssues.reduce((value, issue) => value - (penalties[issue.severity] || 10), 100);
    return Math.max(0, Math.min(100, score));
  }

  function readinessStatus(score, forcedStatus) {
    if (forcedStatus === "red" || score < 50) return "red";
    if (forcedStatus === "yellow" || score < 85) return "yellow";
    return "green";
  }

  function buildReviewSummary(review) {
    const agents = Array.isArray(review.agents) ? review.agents : [];
    const groupedIssues = groupReviewIssues(agents);
    const required = groupedIssues.filter((issue) => severityRank(issue.severity) >= 3).slice(0, 5);
    const recommended = groupedIssues.filter((issue) => severityRank(issue.severity) < 3).slice(0, 5);
    const score = calculateReadinessScore(groupedIssues);
    const status = review.overall_status || readinessStatus(score);
    const hasDiscoveryGap = groupedIssues.some((issue) => ["business outcome", "scope boundaries", "target users"].includes(issue.id));
    const hasDevelopmentGap = groupedIssues.some((issue) => ["acceptance criteria", "dependencies", "testability"].includes(issue.id) || severityRank(issue.severity) >= 3);
    const discoveryReadiness = hasDiscoveryGap ? "yellow" : status === "red" ? "yellow" : "green";
    const developmentReadiness = hasDevelopmentGap ? (required.length ? "red" : "yellow") : status === "red" ? "yellow" : "green";
    const summaryText = groupedIssues.length
      ? "Task needs clarification before development. Focus on the required items first, then use agent details for deeper review."
      : "Task looks ready from the available AI review. Agent details are still available below.";

    return {
      overallStatus: status,
      taskReadinessScore: score,
      discoveryReadiness,
      developmentReadiness,
      summaryText,
      requiredBeforeDevelopment: required,
      recommendedImprovements: recommended,
      topIssues: groupedIssues.slice(0, 5),
    };
  }

  function renderIssueList(title, items, emptyText) {
    const rows = items.length
      ? items.map((item) => `
          <li style="margin:6px 0">
            <div style="font-weight:700;color:rgb(226,232,240)">${escapeHtml(item.title)} <span style="color:${statusColor(item.severity === "high" || item.severity === "critical" ? "red" : "yellow")};font-size:11px;text-transform:uppercase">${escapeHtml(item.severity)}</span></div>
            <div style="color:rgb(148,163,184);font-size:12px">Impacts: ${escapeHtml(item.impactedAgents.join(", ") || "AI Review")}</div>
          </li>
        `).join("")
      : `<li style="color:rgb(148,163,184)">${escapeHtml(emptyText)}</li>`;
    return `
      <div style="border:1px solid rgb(39,43,48);border-radius:8px;padding:10px;background:rgb(15,17,19)">
        <div style="font-weight:800;color:rgb(238,242,246);margin-bottom:6px">${escapeHtml(title)}</div>
        <ul style="margin:0;padding-left:18px">${rows}</ul>
      </div>
    `;
  }

  function createSummaryActionButton(label, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    applyInlineButtonStyle(button);
    button.addEventListener("click", onClick);
    return button;
  }

  function createReviewDisclosure(review, summary) {
    const status = summary.overallStatus || review.overall_status || "yellow";
    const color = statusColor(status);
    const disclosure = document.createElement("details");
    disclosure.id = "aigile-ai-review-disclosure";
    disclosure.style.border = `1px solid ${status === "red" ? "rgba(248,113,113,0.42)" : status === "yellow" ? "rgba(234,179,8,0.42)" : "rgba(34,197,94,0.42)"}`;
    disclosure.style.borderRadius = "10px";
    disclosure.style.background = "rgb(15,17,19)";
    disclosure.style.overflow = "hidden";

    const disclosureSummary = document.createElement("summary");
    disclosureSummary.style.cursor = "pointer";
    disclosureSummary.style.listStyle = "none";
    disclosureSummary.style.display = "flex";
    disclosureSummary.style.alignItems = "center";
    disclosureSummary.style.justifyContent = "space-between";
    disclosureSummary.style.gap = "12px";
    disclosureSummary.style.padding = "10px 12px";
    disclosureSummary.style.userSelect = "none";
    disclosureSummary.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;min-width:0;flex-wrap:wrap">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${color};box-shadow:0 0 0 3px rgba(255,255,255,0.04)"></span>
        <strong style="color:rgb(238,242,246)">AI Review</strong>
        <span style="color:rgb(148,163,184)">Type: ${escapeHtml(review.detected_type || "Task")}</span>
        <span style="color:${color};font-weight:800;text-transform:uppercase">${escapeHtml(status)}</span>
        <span style="color:rgb(203,213,225);font-weight:700">Readiness ${escapeHtml(summary.taskReadinessScore)}%</span>
      </div>
      <span style="color:rgb(148,163,184);font-size:12px;white-space:nowrap">Open details</span>
    `;

    disclosure.addEventListener("toggle", () => {
      const hint = disclosureSummary.querySelector("span:last-child");
      if (hint) hint.textContent = disclosure.open ? "Hide details" : "Open details";
    });

    const body = document.createElement("div");
    body.id = "aigile-ai-review-disclosure-body";
    body.style.padding = "0 12px 12px";
    body.style.borderTop = "1px solid rgb(39,43,48)";
    disclosure.appendChild(disclosureSummary);
    disclosure.appendChild(body);
    return { disclosure, body };
  }

  function renderSummaryBlock(panel, review, summary, mattermostButton, statusBadge) {
    const scoreColor = statusColor(summary.overallStatus);
    const summaryBlock = document.createElement("div");
    summaryBlock.style.border = `1px solid ${summary.overallStatus === "red" ? "rgba(248,113,113,0.42)" : summary.overallStatus === "yellow" ? "rgba(234,179,8,0.42)" : "rgba(34,197,94,0.42)"}`;
    summaryBlock.style.borderRadius = "10px";
    summaryBlock.style.padding = "12px";
    summaryBlock.style.background = "linear-gradient(135deg, rgba(24,26,28,0.98), rgba(17,19,22,0.98))";

    summaryBlock.innerHTML = `
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:12px;flex-wrap:wrap">
        <div>
          <div style="font-weight:800;font-size:15px;color:rgb(238,242,246)">AI Review — ${escapeHtml(summary.overallStatus.toUpperCase())}</div>
          <div style="color:rgb(148,163,184);font-size:12px">Type: ${escapeHtml(review.detected_type || "Task")}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="border:1px solid ${scoreColor};color:${scoreColor};border-radius:999px;padding:2px 8px;font-weight:800;text-transform:uppercase">${escapeHtml(summary.overallStatus)}</span>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:minmax(110px,140px) 1fr;gap:14px;align-items:center">
        <div style="border:1px solid rgb(39,43,48);border-radius:8px;padding:10px;text-align:center;background:rgb(15,17,19)">
          <div style="font-size:28px;font-weight:900;color:${scoreColor}">${escapeHtml(summary.taskReadinessScore)}%</div>
          <div style="font-size:11px;color:rgb(148,163,184);font-weight:700">Task Readiness</div>
        </div>
        <div>
          <div style="color:rgb(226,232,240);font-weight:700;margin-bottom:6px">Short conclusion</div>
          <div style="color:rgb(203,213,225)">${escapeHtml(summary.summaryText)}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
            <span style="color:${statusColor(summary.discoveryReadiness)};font-weight:800">Discovery: ${escapeHtml(summary.discoveryReadiness.toUpperCase())}</span>
            <span style="color:${statusColor(summary.developmentReadiness)};font-weight:800">Development: ${escapeHtml(summary.developmentReadiness.toUpperCase())}</span>
          </div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-top:12px">
        ${renderIssueList("Required before development", summary.requiredBeforeDevelopment, "No blockers found.")}
        ${renderIssueList("Recommended improvements", summary.recommendedImprovements, "No non-blocking improvements found.")}
      </div>
    `;

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.gap = "8px";
    actions.style.flexWrap = "wrap";
    actions.style.marginTop = "12px";
    const sendSummaryButton = createSummaryActionButton("Send summary to comments", async () => {
      sendSummaryButton.disabled = true;
      sendSummaryButton.textContent = "Sending...";
      sendSummaryButton.style.opacity = "0.72";
      try {
        const result = await runApplyReviewSummary(review, summary);
        sendSummaryButton.textContent = "Sent to comments";
        sendSummaryButton.style.color = "rgb(134,239,172)";
        sendSummaryButton.style.borderColor = "rgba(34,197,94,0.55)";
        showToast(`${result.label || "AI-A"}: ${result.issue_key}`, "success");
      } catch (error) {
        sendSummaryButton.disabled = false;
        sendSummaryButton.textContent = "Send summary to comments";
        sendSummaryButton.style.opacity = "1";
        showToast(`Summary comment failed: ${error.message}`, "error");
      }
    });
    const questionsButton = createSummaryActionButton("Create clarification questions", () => {
      const questions = summary.topIssues.map((issue) => `- What should be clarified for: ${issue.title}?`).join("\n") || "- No clarification questions needed.";
      showToast(questions, "success");
    });
    const improveButton = createSummaryActionButton("Improve task with AI", () => {
      showToast("Draft improvement flow is prepared for the next MVP step. Use summary comments or task chat approval for now.", "success");
    });
    const detailsButton = createSummaryActionButton("View agent details", () => {
      const details = panel.querySelector("#aigile-agent-details");
      if (details) {
        details.open = true;
        details.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
    actions.appendChild(improveButton);
    actions.appendChild(questionsButton);
    actions.appendChild(sendSummaryButton);
    actions.appendChild(mattermostButton);
    actions.appendChild(detailsButton);
    summaryBlock.appendChild(actions);
    panel.appendChild(summaryBlock);
  }

  function renderReviewPanel(review) {
    const surface = findIssueDetailSurface();
    if (!surface) return;

    const panel = createReviewPanel();
    panel.style.borderColor = "rgb(47, 51, 56)";
    panel.style.background = "rgb(18, 20, 22)";
    const status = review.overall_status || "yellow";
    const agents = Array.isArray(review.agents) ? review.agents : [];
    panel.dataset.issueKey = review.issue_key || findIssueKey() || "";
    panel.dataset.reviewId = review.review_id || "";
    panel.innerHTML = "";

    const mattermostButton = document.createElement("button");
    mattermostButton.type = "button";
    mattermostButton.textContent = "\u0412 Mattermost";
    applyInlineButtonStyle(mattermostButton);
    mattermostButton.addEventListener("click", async () => {
      mattermostButton.disabled = true;
      mattermostButton.textContent = "\u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u044e...";
      mattermostButton.style.opacity = "0.72";
      try {
        const result = await runStartTaskChat(review);
        mattermostButton.textContent = "\u0412 Mattermost \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e";
        mattermostButton.style.borderColor = "rgba(34, 197, 94, 0.55)";
        mattermostButton.style.color = "rgb(134, 239, 172)";
        showToast(`Mattermost: ${result.issue_key}`, "success");
      } catch (error) {
        mattermostButton.disabled = false;
        mattermostButton.textContent = "\u0412 Mattermost";
        mattermostButton.style.opacity = "1";
        showToast(`Mattermost \u043d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043b: ${error.message}`, "error");
      }
    });
    const statusBadge = document.createElement("span");
    statusBadge.style.border = `1px solid ${statusColor(status)}`;
    statusBadge.style.color = statusColor(status);
    statusBadge.style.borderRadius = "999px";
    statusBadge.style.padding = "2px 8px";
    statusBadge.style.fontWeight = "700";
    statusBadge.style.textTransform = "uppercase";
    statusBadge.textContent = status;
    const reviewSummary = buildReviewSummary(review);
    const disclosure = createReviewDisclosure(review, reviewSummary);
    panel.appendChild(disclosure.disclosure);
    renderSummaryBlock(disclosure.body, review, reviewSummary, mattermostButton, statusBadge);

    const agentDetails = document.createElement("details");
    agentDetails.id = "aigile-agent-details";
    agentDetails.style.marginTop = "12px";
    agentDetails.style.borderTop = "1px solid rgb(39,43,48)";
    agentDetails.style.paddingTop = "10px";
    const agentSummary = document.createElement("summary");
    agentSummary.style.cursor = "pointer";
    agentSummary.style.fontWeight = "800";
    agentSummary.style.color = "rgb(238,242,246)";
    agentSummary.textContent = "Agent Details";
    agentDetails.appendChild(agentSummary);

    let agentIndex = 0;
    for (const agent of agents) {
      const details = document.createElement("details");
      details.style.borderTop = "1px solid rgb(39, 43, 48)";
      details.style.padding = "8px 0";

      const summary = document.createElement("summary");
      summary.style.cursor = "pointer";
      summary.style.listStyle = "none";
      summary.innerHTML = `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${statusColor(agent.status)};margin-right:8px"></span><strong>${escapeHtml(agent.agent_name || "AI Agent")}</strong><span style="margin-left:8px;color:rgb(148,163,184)">${escapeHtml(agent.summary || "")}</span>`;
      details.appendChild(summary);

      const body = document.createElement("div");
      body.style.margin = "8px 0 0 17px";
      body.style.color = "rgb(203, 213, 225)";

      const findings = Array.isArray(agent.findings) ? agent.findings : [];
      if (findings.length) {
        for (const finding of findings) {
          const item = document.createElement("div");
          item.style.margin = "8px 0";
          item.innerHTML = `
            <div style="font-weight:700">${escapeHtml(finding.title || "\u0417\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u0435")} <span style="color:rgb(148,163,184);font-weight:500">(${escapeHtml(finding.severity || "medium")})</span></div>
            <div>${escapeHtml(finding.description || "")}</div>
            <div style="color:rgb(148,163,184)">\u0420\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u044f: ${escapeHtml(finding.recommendation || "")}</div>
          `;
          body.appendChild(item);
        }
      } else {
        body.textContent = "\u0417\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u0439 \u043d\u0435\u0442.";
      }

      const applyButton = document.createElement("button");
      applyButton.type = "button";
      applyButton.textContent = "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0432 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438";
      applySecondaryButtonStyle(applyButton);
      const currentAgentIndex = agentIndex;
      applyButton.addEventListener("click", async () => {
        applyButton.disabled = true;
        applyButton.textContent = "\u041f\u0440\u0438\u043c\u0435\u043d\u044f\u044e...";
        applyButton.style.opacity = "0.7";
        try {
          const result = await runApplySuggestion(review, agent, currentAgentIndex, findings.length ? 0 : null);
          applyButton.textContent = "\u0412 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e";
          applyButton.style.borderColor = "rgba(34, 197, 94, 0.55)";
          applyButton.style.color = "rgb(134, 239, 172)";
          showToast(`${result.label || "AI-A"}: ${result.issue_key}`, "success");
        } catch (error) {
          applyButton.disabled = false;
          applyButton.textContent = "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u0432 \u043a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0438";
          applyButton.style.opacity = "1";
          showToast(`Apply failed: ${error.message}`, "error");
        }
      });
      body.appendChild(applyButton);

      details.appendChild(body);
      agentDetails.appendChild(details);
      agentIndex += 1;
    }
    disclosure.body.appendChild(agentDetails);

    const anchor = surface.querySelector("#aigile-ai-review-panel");
    if (!anchor) {
      const propertiesHeading = Array.from(surface.querySelectorAll("*")).find((element) => {
        const text = (element.innerText || "").trim();
        return text === "\u0421\u0432\u043e\u0439\u0441\u0442\u0432\u0430" || text === "Properties";
      });
      if (propertiesHeading && propertiesHeading.parentElement) {
        propertiesHeading.parentElement.insertBefore(panel, propertiesHeading);
      } else {
        surface.appendChild(panel);
      }
    }
  }

  async function restoreLatestReviewForIssue(issueKey) {
    if (!issueKey) return;
    const existingPanel = document.getElementById("aigile-ai-review-panel");
    if (existingPanel && existingPanel.dataset.issueKey === issueKey && existingPanel.dataset.reviewId) {
      RESTORE_REVIEW_STATE.issueKey = issueKey;
      RESTORE_REVIEW_STATE.reviewId = existingPanel.dataset.reviewId;
      return;
    }
    if (RESTORE_REVIEW_STATE.inFlight === issueKey) return;
    if (RESTORE_REVIEW_STATE.issueKey === issueKey && RESTORE_REVIEW_STATE.reviewId && existingPanel) return;

    RESTORE_REVIEW_STATE.inFlight = issueKey;
    try {
      const review = await fetchLatestReview(issueKey);
      if (!review || !review.review_id) {
        if (existingPanel && existingPanel.dataset.issueKey && existingPanel.dataset.issueKey !== issueKey) {
          existingPanel.remove();
        }
        RESTORE_REVIEW_STATE.issueKey = issueKey;
        RESTORE_REVIEW_STATE.reviewId = null;
        return;
      }
      RESTORE_REVIEW_STATE.issueKey = issueKey;
      RESTORE_REVIEW_STATE.reviewId = review.review_id;
      renderReviewPanel(review);
    } catch (error) {
      RESTORE_REVIEW_STATE.issueKey = issueKey;
      RESTORE_REVIEW_STATE.reviewId = null;
      console.warn("[AIGILE] Failed to restore latest AI review", error);
    } finally {
      if (RESTORE_REVIEW_STATE.inFlight === issueKey) {
        RESTORE_REVIEW_STATE.inFlight = null;
      }
    }
  }

  function renderBlockedPanel(error) {
    const surface = findIssueDetailSurface();
    if (!surface) return;

    const payload = error && error.payload ? error.payload : {};
    const title = payload.title || "\u0410\u043d\u0430\u043b\u0438\u0437 \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d";
    const message =
      payload.message ||
      payload.error ||
      (error && error.message) ||
      "\u0412\u044b\u0431\u0435\u0440\u0438 \u0442\u0438\u043f \u0437\u0430\u0434\u0430\u0447\u0438 \u0447\u0435\u0440\u0435\u0437 \u043c\u0435\u0442\u043a\u0443 Epic, Story, Bug, Task, Tech Debt, Research \u0438\u043b\u0438 Release.";

    const panel = createReviewPanel();
    panel.innerHTML = "";
    panel.style.borderColor = "rgba(248, 113, 113, 0.55)";
    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px">
        <div style="font-weight:700;font-size:14px;color:rgb(254,202,202)">${escapeHtml(title)}</div>
        <span style="border:1px solid rgb(248,113,113);color:rgb(248,113,113);border-radius:999px;padding:2px 8px;font-weight:700;text-transform:uppercase">STOP</span>
      </div>
      <div style="color:rgb(226,232,240)">${escapeHtml(message)}</div>
    `;

    const existing = surface.querySelector("#aigile-ai-review-panel");
    if (!existing) {
      const propertiesHeading = Array.from(surface.querySelectorAll("*")).find((element) => {
        const text = (element.innerText || "").trim();
        return text === "\u0421\u0432\u043e\u0439\u0441\u0442\u0432\u0430" || text === "Properties";
      });
      if (propertiesHeading && propertiesHeading.parentElement) {
        propertiesHeading.parentElement.insertBefore(panel, propertiesHeading);
      } else {
        surface.appendChild(panel);
      }
    }
  }

  async function runAction(action, button) {
    const issueKey = findIssueKey(button);
    if (!issueKey) {
      showToast("Issue key was not found on this Plane page.", "error");
      return;
    }

    setButtonState(button, action, "loading");
    try {
      const data = await action.run(issueKey);
      renderReviewPanel(data);
      const statusText = `AI review ${data.overall_status || "ready"}.`;
      setButtonState(button, action, "success");
      showToast(`${statusText} ${issueKey}`, "success");
      setTimeout(() => setButtonState(button, action, "idle"), 4000);
    } catch (error) {
      renderBlockedPanel(error);
      setButtonState(button, action, "error");
      const message =
        error && error.payload && error.payload.blocked
          ? "\u0410\u043d\u0430\u043b\u0438\u0437 \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d: \u0432\u044b\u0431\u0435\u0440\u0438 \u0442\u0438\u043f \u0437\u0430\u0434\u0430\u0447\u0438 \u0447\u0435\u0440\u0435\u0437 \u043c\u0435\u0442\u043a\u0443."
          : `AI \u0430\u043d\u0430\u043b\u0438\u0437 \u043d\u0435 \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u043b\u0441\u044f: ${error.message}`;
      showToast(message, "error");
      setTimeout(() => setButtonState(button, action, "idle"), 5000);
    }
  }

  function createButton(action) {
    const button = document.createElement("button");
    button.id = action.elementId;
    button.type = "button";
    button.dataset.aigileAction = action.id;
    button.dataset.aigileVersion = PATCH_VERSION;
    button.dataset.state = "idle";
    button.setAttribute("aria-label", action.ariaLabel);
    setButtonState(button, action, "idle");
    button.addEventListener("mouseenter", () => {
      if (!button.disabled && button.dataset.state === "idle") {
        button.style.background = "rgb(31, 34, 38)";
        button.style.borderColor = "rgb(67, 73, 81)";
      }
    });
    button.addEventListener("mouseleave", () => {
      if (!button.disabled && button.dataset.state === "idle") {
        applyButtonStyle(button);
      }
    });
    button.addEventListener("click", () => runAction(action, button));
    return button;
  }

  function removeActionButtons() {
    for (const action of ACTIONS) {
      const existing = document.getElementById(action.elementId);
      if (existing) existing.remove();
    }
  }

  function syncActionButtons() {
    const nativeAction = findNativeActionButton();
    const actionRow = nativeAction ? nativeAction.row : findActionRow();
    window.__aigilePlaneActionsDebug = {
      issueKey: findIssueKey(actionRow),
      actionRowFound: Boolean(actionRow),
      nativeActionFound: Boolean(nativeAction),
      actions: ACTIONS.map((action) => action.id),
      path: window.location.pathname,
      loadedAt: new Date().toISOString(),
    };

    const issueKey = findIssueKey(actionRow);
    if (!actionRow || !issueKey) {
      removeActionButtons();
      return;
    }

    for (const action of ACTIONS) {
      let button = document.getElementById(action.elementId);
      if (button && button.dataset.aigileVersion !== PATCH_VERSION) {
        button.remove();
        button = null;
      }
      if (!button) button = createButton(action);
      if (nativeAction && nativeAction.control.parentElement === actionRow) {
        const next = nativeAction.control.nextSibling;
        if (button.parentElement !== actionRow || next !== button) {
          actionRow.insertBefore(button, nativeAction.control.nextSibling);
        }
      } else if (button.parentElement !== actionRow) {
        actionRow.appendChild(button);
      }
      if (button.dataset.state === "idle") setButtonState(button, action, "idle");
    }
    restoreLatestReviewForIssue(issueKey);
  }

  let syncQueued = false;
  function scheduleSync(delay) {
    if (syncQueued) return;
    syncQueued = true;
    setTimeout(() => {
      syncQueued = false;
      syncActionButtons();
    }, delay);
  }

  const originalPushState = window.__aigilePlaneOriginalPushState || history.pushState;
  window.__aigilePlaneOriginalPushState = originalPushState;
  history.pushState = function () {
    originalPushState.apply(this, arguments);
    scheduleSync(400);
  };

  const onPopState = () => scheduleSync(400);
  window.addEventListener("popstate", onPopState);
  const observer = new MutationObserver(() => scheduleSync(100));
  observer.observe(document.documentElement, { childList: true, subtree: true });
  const intervalId = setInterval(() => scheduleSync(0), 1200);
  window.__aigilePlaneActionsCleanup = function () {
    observer.disconnect();
    clearInterval(intervalId);
    window.removeEventListener("popstate", onPopState);
    history.pushState = originalPushState;
    removeActionButtons();
  };
  scheduleSync(800);
})();
