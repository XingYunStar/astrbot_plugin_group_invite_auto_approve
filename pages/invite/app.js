import { createApi } from "./api.js";

const bridge = window.AstrBotPluginPage;
const api = createApi(bridge);

const DEFAULT_KEYWORDS = ["原神", "星穹铁道", "绝区零"];

const state = {
  config: null,
  baseline: null,
  policy: null,
  stats: null,
  records: [],
  platforms: [],
  meta: null,
  activeTab: "config",
};

const el = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */
function clone(value) {
  return value === undefined ? value : JSON.parse(JSON.stringify(value));
}

function getPath(obj, path) {
  return path.split(".").reduce((cur, key) => (cur == null ? undefined : cur[key]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i += 1) {
    if (typeof cur[keys[i]] !== "object" || cur[keys[i]] === null) {
      cur[keys[i]] = {};
    }
    cur = cur[keys[i]];
  }
  cur[keys[keys.length - 1]] = value;
}

function toast(message, kind = "info", timeout = 3200) {
  const box = el("toasts");
  if (!box) return;
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  box.appendChild(node);
  window.setTimeout(() => node.remove(), timeout);
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function escapeHtml(value) {
  return text(value, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function timeText(record) {
  if (record.time_text) return record.time_text;
  if (!record.timestamp) return "—";
  try {
    return new Date(record.timestamp * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

/* ------------------------------------------------------------------ */
/* 表单绑定                                                            */
/* ------------------------------------------------------------------ */
const controls = () => Array.from(document.querySelectorAll("[data-path]"));

function readControl(node) {
  const type = node.dataset.type || "str";
  if (type === "bool") return node.checked;
  if (type === "int") {
    const value = parseInt(node.value, 10);
    return Number.isNaN(value) ? 0 : value;
  }
  return node.value;
}

function writeControl(node, value) {
  const type = node.dataset.type || "str";
  if (type === "bool") {
    node.checked = Boolean(value);
  } else if (type === "int") {
    node.value = value === null || value === undefined ? "" : String(value);
  } else {
    node.value = value === null || value === undefined ? "" : String(value);
  }
}

function collectConfig() {
  const payload = clone(state.config) || {};
  controls().forEach((node) => {
    setPath(payload, node.dataset.path, readControl(node));
  });
  payload.conditions = payload.conditions || {};
  // 关键词与豁免名单不是普通表单控件，值统一以 state.config 为准
  payload.conditions.keywords = readPathList("conditions.keywords");
  payload.conditions.verify_exempt_users = readPathList(
    "conditions.verify_exempt_users",
  );
  return payload;
}

function applyConfig(config) {
  state.config = clone(config);
  controls().forEach((node) => writeControl(node, getPath(state.config, node.dataset.path)));
  renderKeywords();
  renderExemptUsers();
  refreshDependent();
  updateDirty();
  updateImagePreview();
}

function markDirty() {
  updateDirty();
}

function updateDirty() {
  if (!state.baseline) {
    el("dirtyDot").classList.add("hidden");
    return;
  }
  const current = JSON.stringify(collectConfig());
  const base = JSON.stringify(state.baseline);
  const dirty = current !== base;
  el("dirtyDot").classList.toggle("hidden", !dirty);
}

/* ------------------------------------------------------------------ */
/* 关键词编辑                                                          */
/* ------------------------------------------------------------------ */
function writeKeywords(list) {
  writePathList("conditions.keywords", list);
}

/** 通用标签编辑器：读取/写入某个路径下的字符串列表。 */
function readPathList(path) {
  const list = getPath(state.config, path);
  return Array.isArray(list) ? list.slice() : [];
}

function writePathList(path, list) {
  state.config = state.config || {};
  setPath(state.config, path, list);
  renderChipsFor(path);
  markDirty();
}

function renderChipsFor(path) {
  const spec =
    path === "conditions.keywords"
      ? {
          box: "keywordChips",
          empty: "尚未配置关键词（关键词条件将恒不满足）",
        }
      : { box: "exemptChips", empty: "尚未配置豁免账号" };
  const box = el(spec.box);
  if (!box) return;
  const list = readPathList(path);
  box.innerHTML = "";
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "chip-empty";
    empty.textContent = spec.empty;
    box.appendChild(empty);
    return;
  }
  list.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.appendChild(document.createTextNode(item));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "删除";
    remove.addEventListener("click", () => {
      writePathList(path, readPathList(path).filter((v) => v !== item));
    });
    chip.appendChild(remove);
    box.appendChild(chip);
  });
}

function addItemsFromInput(inputId, path, normalize) {
  const input = el(inputId);
  if (!input) return;
  const raw = input.value.trim();
  if (!raw) return;
  const incoming = raw
    .split(/[\n,，、;；\s]+/)
    .map((item) => (normalize ? normalize(item.trim()) : item.trim()))
    .filter(Boolean);
  const list = readPathList(path);
  incoming.forEach((item) => {
    if (!list.includes(item)) list.push(item);
  });
  input.value = "";
  writePathList(path, list);
}

function renderKeywords() {
  renderChipsFor("conditions.keywords");
}

function renderExemptUsers() {
  renderChipsFor("conditions.verify_exempt_users");
}

function addKeywordsFromInput() {

function addExemptFromInput() {
  addItemsFromInput("exemptInput", "conditions.verify_exempt_users", (v) =>
    /^\d+$/.test(v) ? v : "",
  );
}

/* ------------------------------------------------------------------ */
/* 依赖态与预览                                                        */
/* ------------------------------------------------------------------ */
function refreshDependent() {
  const config = collectConfig();

  // 开关关闭时禁用其下属输入
  document.querySelectorAll("[data-enabled-by]").forEach((container) => {
    const path = container.dataset.enabledBy;
    const enabled = Boolean(getPath(config, path));
    const controller = container.querySelector(`[data-path="${path}"]`);
    container
      .querySelectorAll("input, textarea, select, button")
      .forEach((node) => {
        if (node === controller) return;
        node.disabled = !enabled;
      });
  });

  const keywordOn = Boolean(getPath(config, "conditions.keyword_enable"));
  const countOn = Boolean(getPath(config, "conditions.member_count_enable"));
  el("allOffBanner").classList.toggle("hidden", keywordOn || countOn);

  // 「入群后重新校验」与「群名/人数拿不到时怎么办」是一对强耦合配置：
  // 复核的触发点是“进群”，所以不进群的选项会让复核永远不执行（死配置）。
  const verifyOn = Boolean(getPath(config, "conditions.verify_after_join"));
  const policySelect = el("infoPolicySelect");
  if (policySelect) {
    policySelect.disabled = verifyOn;
    if (verifyOn) {
      policySelect.value = "approve";
      state.config.conditions.when_info_unavailable = "approve";
    }
    renderInfoPolicyLabels(policySelect.value, verifyOn);
  }
  el("infoPolicyConflict").classList.toggle("hidden", !verifyOn);
  el("infoPolicyNotAutoApprove").classList.toggle(
    "hidden",
    policySelect ? policySelect.value !== "approve" : true,
  );
}

/**
 * 这个选项的含义会随「入群后重新校验」而改变，所以标签必须动态生成，
 * 否则用户会把「进群试用」误读成「无差别同意」而不去选它。
 */
function renderInfoPolicyLabels(policy, verifyOn) {
  const select = el("infoPolicySelect");
  if (!select) return;

  const labels = verifyOn
    ? {
        approve:
          "进群试用：先进群 → 按关键词/人数判断 → 不合格自动退出（推荐）",
        skip: "不进群，也不回复邀请人",
      }
    : {
        approve:
          "⚠️ 直接同意并留在群里：不复核、不退出（效果接近对该群无差别同意）",
        skip: "不进群，也不回复邀请人",
      };

  Array.from(select.options).forEach((option) => {
    if (labels[option.value]) option.textContent = labels[option.value];
  });

  // 本项只回答「要不要先进群看一眼」；回复与否不由它决定，必须讲清楚作用范围，
  // 否则用户会以为「不进群」也能全局静默，或与条件不匹配的情况搞混。
  const previews = {
    approve: verifyOn
      ? "机器人会先进群，随即读取真实的群名 / 群介绍 / 群公告 / 人数，" +
        "按已开启的条件重新判断：满足就留下并发欢迎消息；不满足就私聊告知邀请人后自动退群。"
      : "机器人会直接进群并一直留在群里 —— 因为复核已关闭，即使群名和人数不符合条件也不会退出。" +
        "这种状态下它和「自动同意所有群邀请」在这些群上的效果基本一样，请谨慎使用。",
    skip:
      "机器人不会进群，邀请保持待处理（不自动同意、也不自动拒绝，等你人工决定），" +
      "并且不给邀请人任何反馈。\n" +
      "作用范围仅限「读不到群名 / 人数」这一种情况：" +
      "如果读到了群名、只是关键词或人数不匹配，仍会照常回复邀请人 —— " +
      "那是否回复由「未满足条件时对邀请人发送消息」控制。",
  };

  const box = el("infoPolicyPreview");
  const text = el("infoPolicyPreviewText");
  if (!box || !text) return;
  box.classList.toggle("warn", !verifyOn && policy === "approve");
  box.classList.toggle("info", verifyOn || policy !== "approve");
  el("infoPolicyPreviewTitle").textContent = "实际会发生什么";
  text.textContent = previews[policy] || "";
}

function updateImagePreview() {
  const image = el("imagePreview");
  if (!image) return;
  const value = (collectConfig().welcome || {}).image || "";
  const trimmed = String(value).trim();
  if (/^https?:\/\//i.test(trimmed)) {
    image.src = trimmed;
    image.classList.remove("hidden");
  } else {
    image.removeAttribute("src");
    image.classList.add("hidden");
  }
}

function renderPolicy(policy) {
  if (!policy) return;
  const banner = el("policyBanner");
  banner.className = "banner";
  if (policy.mode === "approve_all") {
    banner.classList.add("ok");
    el("policyTitle").textContent = `✅ ${policy.label}`;
  } else if (policy.mode === "disabled") {
    banner.classList.add("warn");
    el("policyTitle").textContent = `⏸ ${policy.label}`;
  } else {
    banner.classList.add("info");
    el("policyTitle").textContent = `⚙️ ${policy.label}`;
  }
  let detail = policy.detail || "";
  if (policy.mode === "conditions") {
    detail += policy.reject
      ? "；未满足时会自动拒绝该邀请。"
      : "；未满足时保持待处理，不做任何操作。";
  }
  el("policyText").textContent = detail;
  el("heroSub").textContent = detail;
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadBootstrap() {
  const data = await api.get("bootstrap");
  state.meta = data.meta || null;
  state.platforms = data.platforms || [];
  state.stats = data.stats || null;
  state.records = data.history || [];
  applyConfig(data.config || {});
  state.baseline = clone(state.config);
  state.policy = data.policy || null;
  renderPolicy(state.policy);
  renderStats();
  renderRecords();
  renderEnv();
  updateDirty();
}

async function refreshRecords() {
  const data = await api.get("history", { limit: 100 });
  state.records = data.records || [];
  state.stats = data.stats || null;
  renderStats();
  renderRecords();
}

/* ------------------------------------------------------------------ */
/* 渲染：统计 / 记录 / 环境                                            */
/* ------------------------------------------------------------------ */
function renderStats() {
  const box = el("statsBox");
  if (!box) return;
  const stats = state.stats || {};
  const items = [
    ["收到的群邀请", stats.invites],
    ["已同意", stats.approved],
    ["已拒绝", stats.rejected],
    ["未处理", stats.ignored],
    ["当前保留记录", stats.kept],
  ];
  box.innerHTML = items
    .map(
      ([label, value]) =>
        `<div class="stat"><div class="num">${escapeHtml(value ?? 0)}</div>` +
        `<div class="lbl">${escapeHtml(label)}</div></div>`,
    )
    .join("");
}

function decisionBadge(record) {
  if (record.source === "join") {
    return '<span class="badge dim">入群通知</span>';
  }
  if (record.approve === true) return '<span class="badge ok">已同意</span>';
  if (record.approve === false) return '<span class="badge err">已拒绝</span>';
  return '<span class="badge warn">未处理</span>';
}

function conditionRows(record) {
  const conditions = Array.isArray(record.conditions) ? record.conditions : [];
  const rows = conditions
    .filter((item) => item.enabled)
    .map((item) => {
      // 三态：满足 / 明确不满足 / 无法判定（协议端没给数据）
      const mark =
        item.evaluable === false ? "❓" : item.passed ? "✅" : "❌";
      const suffix =
        item.evaluable === false ? "（无法判定，不计入不满足）" : "";
      return `${mark} ${escapeHtml(item.label)}：${escapeHtml(item.detail)}${suffix}`;
    });
  if (!rows.length) {
    return '<div class="hint">未启用任何条件（或该记录来自入群通知）</div>';
  }
  return rows.map((row) => `<div>${row}</div>`).join("");
}

function groupDetailHtml(group) {
  if (!group) return "";
  const notices = Array.isArray(group.notices) ? group.notices : [];
  const errors = Array.isArray(group.errors) ? group.errors : [];
  const rows = [
    ["群号", group.group_id],
    ["群名称", group.name || "（未获取到）"],
    ["群备注", group.remark || "（无）"],
    [
      "群人数",
      group.max_member_count
        ? `${group.member_count} / ${group.max_member_count}`
        : group.member_count,
    ],
    ["建群时间", group.create_time_text || "未知"],
    ["群等级", group.level],
    ["全员禁言", group.all_shut ? "是" : "否"],
    ["群主", group.owner_id ? `${group.owner_name || "未知"}(${group.owner_id})` : "（未获取到）"],
    [
      "管理员",
      Array.isArray(group.admin_ids) && group.admin_ids.length
        ? group.admin_ids.join("、")
        : "（未获取到）",
    ],
    ["群介绍", group.memo || "（无）"],
    ["群公告", notices.length ? notices.join("\n---\n") : "（无 / 未获取到）"],
  ];

  if (group.info_available === false) {
    rows.unshift([
      "⚠️ 群资料",
      "不可用 —— 机器人不在该群，协议端只返回空壳（QQ 侧限制）",
    ]);
  }
  if (group.is_member === true) {
    rows.unshift(["成员状态", "机器人已在该群内"]);
  }

  if (group.inviter_id) {
    rows.push([
      "邀请人",
      group.inviter_name
        ? `${group.inviter_name}(${group.inviter_id})`
        : String(group.inviter_id),
    ]);
  }

  let html = '<dl class="kv">';
  rows.forEach(([key, value]) => {
    html += `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`;
  });
  html += "</dl>";

  if (errors.length) {
    html +=
      '<div class="hint" style="padding: 0 12px 10px">读取失败项：' +
      errors.map((item) => escapeHtml(item)).join("；") +
      "</div>";
  }
  return html;
}

function renderRecords() {
  const box = el("recordsBox");
  if (!box) return;
  const records = state.records || [];
  if (!records.length) {
    box.innerHTML = '<div class="empty">暂无记录</div>';
    return;
  }

  box.innerHTML = records
    .map((record, index) => {
      const group = record.group || {};
      const title = `${escapeHtml(group.name || "未知群名")} (${escapeHtml(group.group_id)})`;
      const meta = [
        timeText(record),
        record.inviter_id ? `邀请人 ${record.inviter_id}` : "",
        record.comment ? `验证信息：${record.comment}` : "",
      ]
        .filter(Boolean)
        .join(" · ");

      const actions = Array.isArray(record.actions) ? record.actions : [];
      return `
        <div class="record">
          <div class="record-head">
            <div class="record-title">${title}</div>
            ${decisionBadge(record)}
            <div class="record-meta">${escapeHtml(meta)}</div>
          </div>
          <div style="padding: 0 12px 10px">
            <div class="hint" style="margin-bottom: 6px">${escapeHtml(record.reason || "")}</div>
            ${conditionRows(record)}
            ${
              actions.length
                ? `<div class="hint">执行动作：${actions.map((a) => escapeHtml(a)).join("；")}</div>`
                : ""
            }
          </div>
          <details class="record-detail">
            <summary>展开 / 收起该群全部信息（第 ${index + 1} 条）</summary>
            ${groupDetailHtml(group)}
          </details>
        </div>`;
    })
    .join("");
}

function renderEnv() {
  const box = el("envBox");
  if (!box) return;
  const meta = state.meta || {};
  const platforms = state.platforms || [];
  const items = [
    ["插件名", meta.pluginName],
    ["插件版本", meta.version],
    ["已连接 aiocqhttp 平台", platforms.length ? platforms.length : "0"],
  ];
  let html = '<dl class="kv">';
  items.forEach(([key, value]) => {
    html += `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`;
  });
  html += "</dl>";
  if (platforms.length) {
    html +=
      '<div class="hint" style="padding: 0 12px 12px">' +
      platforms
        .map(
          (p) =>
            `${escapeHtml(p.name || p.id)}${p.self_id ? `（${escapeHtml(p.self_id)}）` : ""}`,
        )
        .join("、") +
      "</div>";
  } else {
    html +=
      '<div class="hint" style="padding: 0 12px 12px">当前没有已连接的 aiocqhttp 平台，「群号体检」将不可用。</div>';
  }
  box.innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* 决策结果卡片                                                        */
/* ------------------------------------------------------------------ */
function renderDecision(container, group, decision) {
  if (!container) return;
  const approveText =
    decision.approve === true
      ? '<span class="badge ok">会自动同意</span>'
      : decision.approve === false
        ? '<span class="badge err">会自动拒绝</span>'
        : '<span class="badge warn">不处理</span>';

  const conditions = (decision.conditions || [])
    .filter((item) => item.enabled)
    .map((item) => {
      const mark =
        item.evaluable === false ? "❓" : item.passed ? "✅" : "❌";
      const suffix =
        item.evaluable === false ? "（无法判定，不计入不满足）" : "";
      return `<div>${mark} <strong>${escapeHtml(item.label)}</strong>：${escapeHtml(item.detail)}${suffix}</div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="record">
      <div class="record-head">
        <div class="record-title">${escapeHtml(group.name || "未知群名")} (${escapeHtml(group.group_id)})</div>
        ${approveText}
      </div>
      <div style="padding: 0 12px 10px">
        <div class="hint" style="margin-bottom: 6px">${escapeHtml(decision.reason || "")}</div>
        ${conditions || '<div class="hint">未启用任何条件</div>'}
        ${
          decision.matched_keywords && decision.matched_keywords.length
            ? `<div class="hint">命中关键词：${escapeHtml(decision.matched_keywords.join("、"))}</div>`
            : ""
        }
      </div>
      <details class="record-detail" open>
        <summary>该群全部信息</summary>
        ${groupDetailHtml(group)}
      </details>
    </div>`;
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.activeTab = tab.dataset.tab;
      document
        .querySelectorAll(".tab")
        .forEach((node) => node.classList.toggle("active", node === tab));
      document.querySelectorAll("[data-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.dataset.panel !== state.activeTab);
      });
      el("actionbar").classList.toggle("hidden", state.activeTab !== "config");
      if (state.activeTab === "records") {
        refreshRecords().catch((error) => toast(error.message, "err"));
      }
    });
  });
}

function bindForm() {
  controls().forEach((node) => {
    node.addEventListener("change", () => {
      markDirty();
      refreshDependent();
      updateImagePreview();
    });
    if (node.tagName === "INPUT" && node.type === "text") {
      node.addEventListener("input", () => {
        markDirty();
        updateImagePreview();
      });
    }
  });

  el("addKeywordBtn").addEventListener("click", addKeywordsFromInput);
  el("keywordInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addKeywordsFromInput();
    }
  });
  el("resetKeywordBtn").addEventListener("click", () => {
    writeKeywords(DEFAULT_KEYWORDS.slice());
    refreshDependent();
  });

  el("addExemptBtn").addEventListener("click", addExemptFromInput);
  el("exemptInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addExemptFromInput();
    }
  });
}

function bindActions() {
  el("saveBtn").addEventListener("click", async () => {
    const button = el("saveBtn");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const payload = collectConfig();
      const data = await api.post("config", { config: payload });
      applyConfig(data.config || payload);
      state.baseline = clone(state.config);
      state.policy = data.policy || null;
      renderPolicy(state.policy);
      refreshDependent();
      updateDirty();
      toast("配置已保存并生效", "ok");
    } catch (error) {
      toast(error.message || "保存失败", "err", 5000);
    } finally {
      button.disabled = false;
      button.textContent = "保存配置";
    }
  });

  el("reloadBtn").addEventListener("click", async () => {
    try {
      const data = await api.get("config");
      applyConfig(data.config || {});
      state.baseline = clone(state.config);
      state.policy = data.policy || null;
      renderPolicy(state.policy);
      toast("已放弃未保存的修改", "ok");
    } catch (error) {
      toast(error.message, "err");
    }
  });

  el("refreshRecordsBtn").addEventListener("click", () => {
    refreshRecords()
      .then(() => toast("记录已刷新", "ok"))
      .catch((error) => toast(error.message, "err"));
  });

  el("clearRecordsBtn").addEventListener("click", async () => {
    if (!window.confirm("确定要清空全部邀请记录吗？该操作不可撤销。")) return;
    try {
      const data = await api.post("history/clear", {});
      state.stats = data.stats || null;
      state.records = [];
      renderStats();
      renderRecords();
      toast("记录已清空", "ok");
    } catch (error) {
      toast(error.message, "err");
    }
  });

  el("scanBtn").addEventListener("click", async () => {
    const groupId = el("scanGroupInput").value.trim();
    if (!/^\d+$/.test(groupId)) {
      toast("请输入纯数字群号", "err");
      return;
    }
    const button = el("scanBtn");
    button.disabled = true;
    button.textContent = "查询中…";
    try {
      const data = await api.post("scan", {
        group_id: groupId,
        fetch_notice: el("scanFetchNotice").checked,
        fetch_roster: el("scanFetchRoster").checked,
      });
      renderDecision(el("scanResult"), data.group || {}, data.decision || {});
    } catch (error) {
      el("scanResult").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
      toast(error.message, "err", 5000);
    } finally {
      button.disabled = false;
      button.textContent = "查询";
    }
  });

  el("simulateBtn").addEventListener("click", async () => {
    const toInt = (id) => {
      const value = parseInt(el(id).value, 10);
      return Number.isNaN(value) ? 0 : value;
    };
    try {
      const data = await api.post("simulate", {
        group_id: toInt("simGroupId"),
        name: el("simName").value,
        member_count: toInt("simMemberCount"),
        max_member_count: toInt("simMaxMemberCount") || 3000,
        memo: el("simMemo").value,
        notices: el("simNotices").value.split("\n").filter(Boolean),
      });
      renderDecision(el("simulateResult"), data.group || {}, data.decision || {});
    } catch (error) {
      toast(error.message, "err", 5000);
    }
  });
}

function bindTheme() {
  const root = document.documentElement;
  const apply = (context) => {
    if (context && typeof context.isDark === "boolean") {
      root.setAttribute("data-theme", context.isDark ? "dark" : "light");
    }
  };
  apply(bridge.getContext());
  bridge.onContext(apply);
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function main() {
  bindTabs();
  bindForm();
  bindActions();
  bindTheme();

  try {
    await bridge.ready();
  } catch {
    // 拿不到 context 也不影响接口调用，继续
  }

  try {
    await loadBootstrap();
  } catch (error) {
    el("heroSub").textContent = "加载失败";
    toast(error.message || "加载配置失败", "err", 6000);
  }
}

main();
