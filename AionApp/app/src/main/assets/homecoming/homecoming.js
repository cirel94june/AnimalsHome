(() => {
  "use strict";
  const api = window.HomecomingNative;
  const state = {
    timeline: "group",
    page: "chat",
    requestId: "",
    bootstrap: null,
    enterArmed: false,
    returnArmed: false,
    returnDismissed: false,
    memoryOwner: "main",
    memories: []
  };
  const byId = id => document.getElementById(id);
  const uuid = () => "hc-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  // 只认快照里带来的图片数据，别的地址一律当没有（断网也加载不了）
  const safeAvatar = value => /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*$/.test(value || "") ? value : "";

  function avatar(name, image, size) {
    const node = el("span", "avatar" + (size ? " " + size : ""));
    const src = safeAvatar(image);
    if (src) node.style.backgroundImage = `url("${src}")`;
    else node.textContent = Array.from(String(name || "?").trim())[0] || "?";
    return node;
  }

  function readiness() {
    try { return JSON.parse(api.getReadinessJson()); }
    catch (_) { return { ready: false, warning: "归巢的数据暂时读不出来" }; }
  }

  function fillSelect(select, rows, labelKey, valueKey) {
    select.textContent = "";
    rows.forEach(row => {
      const option = document.createElement("option");
      option.value = row[valueKey];
      option.textContent = row[labelKey];
      select.appendChild(option);
    });
  }

  function timeLabel(value) {
    const ms = Number(value || 0) > 1e12 ? Number(value) : Number(value || 0) * 1000;
    if (!ms) return "还没准备过";
    const date = new Date(ms);
    const today = new Date();
    const hhmm = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return date.toDateString() === today.toDateString()
      ? `今天 ${hhmm}` : `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${hhmm}`;
  }

  function checkRow(root, label, value, ok) {
    const row = el("div", "check-row" + (ok ? "" : " bad"));
    row.append(el("span", "", label), el("span", "", value));
    root.appendChild(row);
  }

  function renderReadiness() {
    const data = readiness();
    const members = byId("members");
    members.textContent = "";
    (data.members || []).forEach(member => {
      const box = el("div", "member");
      box.append(avatar(member.name, member.avatar), el("b", "", member.name || ""),
        el("small", "", `记得 ${Number(member.memoryCount || 0)} 件事`));
      members.appendChild(box);
    });
    const messages = Number(data.mainMessageCount || 0) + Number(data.secondMessageCount || 0)
      + Number(data.groupMessageCount || 0);
    const routes = Number(data.portableRouteCount || 0);
    const summary = byId("summary");
    summary.textContent = "";
    checkRow(summary, "最近的聊天", messages > 0 ? `带上了 ${messages} 条 ✓` : "还没有", messages > 0);
    checkRow(summary, "能用的网络线路", routes > 0 ? `${routes} 条 ✓` : "还没有", routes > 0);
    checkRow(summary, "上次准备好", timeLabel(data.snapshotCreatedAt), Boolean(data.snapshotCreatedAt));
    byId("warning").textContent = data.ready ? "" : (data.warning || "还没准备好，连上网后会自动准备");
    byId("enter").disabled = !data.ready;
  }

  function initializeChat() {
    try { state.bootstrap = JSON.parse(api.getBootstrapJson()); }
    catch (_) { state.bootstrap = null; }
    if (!state.bootstrap || !state.bootstrap.identity) return;
    const routes = state.bootstrap.routes || [];
    const writers = roster().filter(member => member.ownerId === "main" || member.ownerId === "second");
    // 提醒只有原来两位能定；每位 AI（含座位）都能私聊、进群、选自己的线路、看自己的记忆
    fillSelect(byId("scheduleOwner"), writers.map(member =>
      ({ id: member.ownerId, label: `${member.name} 来提醒` })), "label", "id");
    syncSchedulePrivateOption();
    fillSelect(byId("settingsOwner"), roster().map(member =>
      ({ id: member.ownerId, label: member.name })), "label", "id");
    renderPeopleTabs();
    renderMemoryTabs();
    fillSelect(byId("routeSelect"), routes, "label", "routeId");
    renderTtsState();
    switchTimeline(state.timeline);
    switchPage(state.page);
    applyRoutePreference();
    updateModels();
  }

  function renderTtsState() {
    const enabled = Boolean(state.bootstrap && state.bootstrap.ttsEnabled);
    byId("ttsEnabled").checked = enabled;
    const quick = byId("ttsQuickToggle");
    quick.textContent = enabled ? "🔊 朗读：开" : "🔇 朗读：关";
    quick.setAttribute("aria-pressed", String(enabled));
    document.querySelectorAll(".tts-replay").forEach(button => {
      button.disabled = !enabled;
      button.title = enabled ? "再读一遍" : "先打开朗读";
    });
  }

  function updateTtsEnabled(enabled) {
    if (!state.bootstrap) return;
    state.bootstrap.ttsEnabled = Boolean(enabled);
    api.setTtsEnabled(state.bootstrap.ttsEnabled);
    renderTtsState();
  }

  function roster() {
    const identity = state.bootstrap?.identity || {};
    if (Array.isArray(identity.roster) && identity.roster.length) return identity.roster;
    return [
      { ownerId: "main", timelineId: "main_private", name: identity.mainName || "主伴侣" },
      { ownerId: "second", timelineId: "companion_private", name: identity.secondName || "第二伴侣" }
    ];
  }

  function memberOf(owner) {
    return roster().find(member => member.ownerId === owner) || null;
  }

  function nameOf(owner) {
    return memberOf(owner)?.name || "AI";
  }

  // 服务器带来的旧消息用家里的名字（aion / connor），手机上新聊的用 main / second
  function ownerOfSender(sender) {
    return { aion: "main", connor: "second" }[sender] || sender;
  }

  function renderPeopleTabs() {
    const root = byId("peopleTabs");
    root.textContent = "";
    roster().forEach(member => {
      const button = el("button", "person-tab");
      button.dataset.timeline = member.timelineId;
      button.append(avatar(member.name, member.avatar, "tiny"), el("span", "", member.name));
      root.appendChild(button);
    });
    const group = el("button", "person-tab group", "群聊");
    group.dataset.timeline = "group";
    root.appendChild(group);
  }

  function renderMemoryTabs() {
    const root = byId("memoryTabs");
    root.textContent = "";
    roster().forEach(member => {
      const button = el("button", "chip", member.name);
      button.dataset.memoryOwner = member.ownerId;
      root.appendChild(button);
    });
    if (!memberOf(state.memoryOwner)) state.memoryOwner = "main";
  }

  // 还没在归巢里选过线路时：先找带着 TA 平时那个模型的线路，没有再用第一条
  function defaultRouteFor(owner) {
    const routes = state.bootstrap?.routes || [];
    const usual = memberOf(owner)?.modelKey || "";
    return routes.find(item => usual && (item.modelKeys || []).includes(usual)) || routes[0] || null;
  }

  function applyRoutePreference() {
    const owner = byId("settingsOwner").value || "main";
    const preference = state.bootstrap?.routePreferences?.[owner] || {};
    const routes = state.bootstrap?.routes || [];
    const chosen = routes.some(item => item.routeId === preference.routeId)
      ? preference.routeId
      : defaultRouteFor(owner)?.routeId || "";
    byId("routeSelect").value = chosen;
  }

  function updateModels() {
    const route = (state.bootstrap?.routes || [])
      .find(item => item.routeId === byId("routeSelect").value);
    const models = (route?.modelKeys || []).map(key => ({ key, label: key }));
    fillSelect(byId("modelSelect"), models, "label", "key");
    const owner = byId("settingsOwner").value || "main";
    const preferred = state.bootstrap?.routePreferences?.[owner]?.modelId
      || memberOf(owner)?.modelKey || "";
    if (models.some(item => item.key === preferred)) {
      byId("modelSelect").value = preferred;
    }
  }

  function saveRoutePreference() {
    const owner = byId("settingsOwner").value || "main";
    const routeId = byId("routeSelect").value;
    const modelId = byId("modelSelect").value;
    if (!routeId || !modelId) return;
    api.setRoutePreference(owner, routeId, modelId);
    state.bootstrap.routePreferences ||= {};
    state.bootstrap.routePreferences[owner] = { routeId, modelId };
  }

  function switchTimeline(timeline) {
    const member = roster().find(item => item.timelineId === timeline);
    if (!member && timeline !== "group") timeline = "group";  // 上次聊的座位这次没带来
    state.timeline = timeline;
    byId("composer").placeholder = member ? `和${member.name}说点什么…` : "和大家说点什么…";
    if (member) {
      // 切到谁的私聊，设置里就显示谁的线路和模型，发送时用的也是 TA 的
      byId("settingsOwner").value = member.ownerId;
      applyRoutePreference();
      updateModels();
    }
    document.querySelectorAll("[data-timeline]").forEach(item =>
      item.classList.toggle("active", item.dataset.timeline === timeline));
    loadMessages();
  }

  function switchPage(page) {
    state.page = page;
    document.querySelectorAll("[data-page-panel]").forEach(item =>
      item.classList.toggle("active", item.dataset.pagePanel === page));
    document.querySelectorAll("[data-page]").forEach(item =>
      item.classList.toggle("active", item.dataset.page === page));
    // 顶上换人的那排只在聊天页有用；别的页有自己的选择，免得两排人看着重复
    byId("peopleTabs").classList.toggle("hidden", page !== "chat");
    if (page === "memory") loadMemories();
    if (page === "schedule") renderSchedules();
  }

  function loadMessages() {
    let rows = [];
    try { rows = JSON.parse(api.getMessagesJson(state.timeline, 0, 160)); }
    catch (_) {}
    const root = byId("messages");
    root.textContent = "";
    rows.forEach(appendMessage);
    root.scrollTop = root.scrollHeight;
  }

  function appendMessage(row) {
    const assistant = Boolean(row.assistant);
    const item = el("article", "message-row " + (assistant ? "assistant" : "user"));
    const body = el("div", "message-body");
    if (assistant) {
      const privateMember = roster().find(member => member.timelineId === state.timeline);
      const member = memberOf(ownerOfSender(row.sender)) || privateMember;
      const name = member?.name || "AI";
      item.appendChild(avatar(name, member?.avatar, "small"));
      if (state.timeline === "group") body.appendChild(el("span", "message-name", name));
    }
    body.appendChild(el("div", "bubble", row.text || ""));
    if (assistant && row.id) {
      const replay = el("button", "tts-replay", "🔊 再读一遍");
      replay.disabled = !Boolean(state.bootstrap && state.bootstrap.ttsEnabled);
      replay.addEventListener("click", () => api.replayTts(row.id));
      body.appendChild(replay);
    }
    item.appendChild(body);
    byId("messages").appendChild(item);
  }

  function setTyping(owner) {
    const typing = byId("typing");
    if (!owner) { typing.classList.add("hidden"); return; }
    const member = memberOf(owner);
    const holder = byId("typingAvatar");
    holder.replaceWith(Object.assign(avatar(member?.name || "AI", member?.avatar, "small"), { id: "typingAvatar" }));
    byId("typingText").textContent = state.timeline === "group" ? "大家正在想…" : "正在想…";
    typing.classList.remove("hidden");
  }

  function send() {
    const text = byId("composer").value.trim();
    if (!text && !byId("mediaState").textContent) return;
    state.requestId = uuid();
    const member = roster().find(item => item.timelineId === state.timeline);
    const owner = member ? member.ownerId : (byId("settingsOwner").value || "main");
    appendMessage({ text, sender: state.bootstrap.identity.userName, assistant: false });
    byId("messages").scrollTop = byId("messages").scrollHeight;
    byId("composer").value = "";
    byId("mediaState").textContent = "";
    closeAttach();
    setTyping(owner);
    window.HomecomingNative.sendMessage(state.requestId, state.timeline, owner, text,
      byId("routeSelect").value, byId("modelSelect").value);
  }

  function closeAttach() {
    byId("attachMenu").classList.add("hidden");
    byId("attachToggle").setAttribute("aria-expanded", "false");
  }

  function loadMemories() {
    document.querySelectorAll("[data-memory-owner]").forEach(item =>
      item.classList.toggle("active", item.dataset.memoryOwner === state.memoryOwner));
    try { state.memories = JSON.parse(api.getMemoriesJson(state.memoryOwner, "")); }
    catch (_) { state.memories = []; }
    // 只有原来两位能在手机上新记；座位只能看从家里带来的
    const writable = state.memoryOwner === "main" || state.memoryOwner === "second";
    byId("showMemoryCompose").classList.toggle("hidden", !writable);
    if (!writable) byId("memoryCompose").classList.add("hidden");
    renderMemories();
  }

  function memoryCard(row, owner) {
    const readonly = String(row.id || "").toLowerCase().startsWith("hub:");
    const item = el("article", "memory-item" + (readonly ? " readonly" : ""));
    item.appendChild(el("p", "", row.content));
    const actions = el("div", "memory-actions");
    if (readonly) {
      // Hub 记忆包是只读的：离线时改了也不会带回去（10-31，Hub #123）
      actions.appendChild(el("span", "memory-readonly", "从家里带来的 · 只能看"));
      item.appendChild(actions);
      return item;
    }
    const edit = el("button", "", "改一下");
    edit.addEventListener("click", () => {
      const changed = window.prompt("改成：", row.content);
      if (changed !== null && changed.trim()) {
        window.HomecomingNative.updateMemory(
          owner, row.id, changed.trim(), row.baseHash || "local");
        loadMemories();
      }
    });
    const remove = el("button", "", "删掉");
    remove.addEventListener("click", () => {
      if (window.confirm("删掉这件事？")) {
        window.HomecomingNative.deleteMemory(owner, row.id, row.baseHash || "local");
        loadMemories();
      }
    });
    actions.append(edit, remove, el("span", "note", "回家时会带回去"));
    item.appendChild(actions);
    return item;
  }

  function renderMemories() {
    const owner = state.memoryOwner;
    const query = byId("memorySearch").value.trim().toLowerCase();
    const rows = state.memories.filter(row => !query || String(row.content || "").toLowerCase().includes(query));
    const isHub = row => String(row.id || "").toLowerCase().startsWith("hub:");
    const mine = rows.filter(row => !isHub(row));
    const fromHome = rows.filter(isHub);
    const root = byId("memoryList");
    root.textContent = "";
    if (mine.length) {
      root.appendChild(el("p", "list-label", "这次在手机上记的"));
      mine.forEach(row => root.appendChild(memoryCard(row, owner)));
    }
    if (fromHome.length) {
      root.appendChild(el("p", "list-label", "从家里带来的（只能看）"));
      fromHome.forEach(row => root.appendChild(memoryCard(row, owner)));
    }
    if (!rows.length) {
      root.appendChild(el("p", "empty", query ? "没找到这件事" : `${nameOf(owner)} 这里还没有记忆`));
    }
  }

  function renderSchedules() {
    let rows = [];
    try { rows = JSON.parse(window.HomecomingNative.listSchedules()); } catch (_) {}
    const exactness = state.bootstrap?.scheduleExactness || "unknown";
    byId("scheduleExactness").textContent = exactness === "exact"
      ? "会准时提醒你。"
      : exactness === "inexact"
        ? "可能会晚一点点；想准时的话，到手机系统设置里允许这个 App「闹钟和提醒」。"
        : "";
    const labels = { alarm: "闹钟", reminder: "提醒", monitor: "看一眼摄像头" };
    const root = byId("scheduleList");
    root.textContent = "";
    if (!rows.length) root.appendChild(el("p", "empty", "还没有提醒"));
    rows.forEach(row => {
      const item = el("article", "schedule-item");
      item.append(
        el("strong", "", `${labels[row.type] || row.type} · ${nameOf(row.ownerId)}`),
        el("p", "", row.content || ""),
        el("small", "", new Date(row.triggerAt).toLocaleString()));
      const remove = el("button", "", "取消这个提醒");
      remove.addEventListener("click", () => {
        if (window.confirm("取消这个提醒？")) {
          window.HomecomingNative.deleteSchedule(row.id);
          renderSchedules();
        }
      });
      item.appendChild(remove);
      root.appendChild(item);
    });
  }

  function renderSupervisionStatus() {
    let data = { enabled: false, readiness: "unavailable", groups: [] };
    try {
      data = JSON.parse(window.HomecomingNative.getSupervisionStatusJson());
    } catch (_) {}
    const readinessLabels = {
      ready: "手机监督在正常工作",
      degraded: "手机监督有些权限没开，可能看不全",
      unavailable: "手机监督现在看不到"
    };
    byId("supervisionReadiness").textContent = data.enabled
      ? (readinessLabels[data.readiness] || "状态不明")
      : "手机监督没有打开";
    const root = byId("supervisionList");
    root.textContent = "";
    (data.groups || []).forEach(group => {
      const item = el("article", "supervision-item");
      item.append(
        el("strong", "", group.displayName || group.groupId || "一组 App"),
        el("p", "", `谁来盯：${group.roleLabel || "还没定"}`),
        el("p", "", `这一轮用了 ${Math.floor((group.roundUsageMs || 0) / 60000)} 分钟`),
        el("small", "", `到 ${(group.checkpointsMinutes || []).join("、") || "—"} 分钟时会提醒`));
      root.appendChild(item);
    });
  }

  function returnState() {
    try { return JSON.parse(api.getReturnStateJson()); }
    catch (_) {
      return {
        open: true,
        phase: "failed",
        failure: "local_package_unavailable",
        counts: {},
        canRetry: true,
        canReturnWithoutSync: false,
        canAbandon: false
      };
    }
  }

  function renderReturn() {
    const data = returnState();
    const panel = byId("returnPanel");
    panel.classList.toggle("hidden", !data.open || state.returnDismissed);
    if (!data.open || state.returnDismissed) return;

    // 四步：收拾好 → 送到家门口 → 核对 → 放进家里
    const order = {
      freezing: 0,
      building: 0,
      frozen: 1,
      saved: 1,
      uploading: 1,
      returning: 1,
      planning: 2,
      applying: 3,
      verifying: 3,
      confirming: 3,
      complete: 4
    };
    const position = order[data.phase] ?? -1;
    byId("returnPhases").querySelectorAll("li").forEach((item, index) => {
      item.classList.toggle("active", index === position);
      item.classList.toggle("done", index < position);
    });
    const phaseLabels = {
      idle: "网通了。把这次在手机上聊的、记的，带回家里。",
      ready: "网通了。把这次在手机上聊的、记的，带回家里。",
      discarding: "正在清掉手机上的归巢数据…",
      freezing: "正在收拾…",
      building: "正在收拾…",
      frozen: "收拾好了，存在手机上。",
      saved: "收拾好了，正在回正常模式…",
      uploading: "正在送回家…",
      returning: "正在送回家…",
      planning: "家里正在核对…",
      applying: "正在放进家里…",
      verifying: "正在确认都放好了…",
      confirming: "正在确认都放好了…",
      complete: "都带回家了，正在回正常模式…",
      failed: "这次没带回去，东西还在手机上。"
    };
    byId("returnStatus").textContent = phaseLabels[data.phase]
      || "网通了。把这次在手机上聊的、记的，带回家里。";
    const failureLabels = {
      package_build_failed: "收拾的时候出了点问题，归巢照常能用，等会儿再试。",
      server_retryable: "家里那边正忙，等一会儿再试一次。",
      receipt_mismatch: "家里回的确认对不上，手机上的东西先不动，等会儿再试。",
      return_sync_failed: "网络或家里暂时连不上，东西都留在手机上了。",
      upload_package_mismatch: "送回去的包对不上，已经停下，手机上的东西没动。",
      apply_incomplete: "只放进去一部分，再试一次就好。",
      no_pending_package: "手机上没有要带回去的东西。",
      local_package_unavailable: "手机上收拾好的东西暂时读不出来。",
      local_discard_failed: "没能清掉手机上的数据，还在归巢里。"
    };
    byId("returnFailure").textContent =
      failureLabels[data.failure] || (data.failure ? "这次没带回去，东西还在手机上。" : "");
    const counts = byId("returnCounts");
    counts.textContent = "";
    const countLabels = {
      apply: "放进家里",
      duplicate: "家里本来就有",
      server_wins: "以家里为准",
      skip: "不用带",
      quarantine: "先放一边等核对",
      invalid: "读不懂，没带"
    };
    let shown = 0;
    Object.keys(countLabels).forEach(key => {
      if (Object.prototype.hasOwnProperty.call(data.counts || {}, key)) {
        checkRow(counts, countLabels[key], `${data.counts[key]} 条`, true);
        shown++;
      }
    });
    counts.classList.toggle("hidden", shown === 0);
    const mayStart = !data.inFlight && !data.failure
      && (data.mode === "active" || (data.pendingPackageCount || 0) > 0);
    const startButton = byId("startReturnSync");
    startButton.textContent = data.mode === "active"
      ? "带回家，退出归巢"
      : "接着带回家";
    startButton.classList.toggle("hidden", !mayStart);
    byId("retryReturnSync").classList.toggle("hidden", !data.canRetry);
    byId("returnWithoutSync").classList.toggle(
      "hidden", !data.canReturnWithoutSync);
    byId("abandonHomecoming").classList.toggle("hidden", !data.canAbandon);
    byId("closeReturnPanel").classList.toggle(
      "hidden", data.mode !== "active" || Boolean(data.inFlight));  // 不能传 undefined，那样会变成来回切换
  }

  function render() {
    const active = Boolean(api.isActive());
    byId("readinessCard").classList.toggle("hidden", active);
    byId("chatShell").classList.toggle("hidden", !active);
    if (active) initializeChat(); else renderReadiness();
    renderReturn();
  }

  function onNativeEvent(event) {
    if (event.type === "complete") {
      setTyping("");
      appendMessage({ id: event.value, text: event.text, sender: "", assistant: true });
      byId("messages").scrollTop = byId("messages").scrollHeight;
      state.requestId = "";
    } else if (event.type === "failure") {
      setTyping("");
      byId("mediaState").textContent = "这次没回上来：" + (event.value || "网络不太好，再发一次试试");
      state.requestId = "";
    } else if (event.type === "group_reply_complete") {
      loadMessages();
    } else if (event.type === "group_reply_failure") {
      byId("mediaState").textContent = `${nameOf(event.ownerId)} 这次没回上来`;
    } else if (event.type === "group_complete") {
      setTyping("");
      state.requestId = "";
      byId("mediaState").textContent = "";
      loadMessages();
    } else if (event.type === "media_ready" || event.type === "media_failure") {
      byId("mediaState").textContent = event.value || "";
    } else if (event.type === "summary_all_result") {
      const results = event.results || {};
      const values = [results.main, results.second].filter(Boolean);
      const processed = values.reduce((sum, value) =>
        sum + Number(value.processed || 0), 0);
      const created = values.reduce((sum, value) =>
        sum + Number(value.created || 0), 0);
      const failed = values.some(value => value.status === "summary_failed");
      byId("mediaState").textContent = failed
        ? `整理了一部分：看了 ${processed} 条聊天，记下 ${created} 件事`
        : processed > 0
          ? `整理好了：看了 ${processed} 条聊天，记下 ${created} 件事`
          : "今天还没有新的聊天要整理";
      if (state.page === "memory") loadMemories();
    } else if (event.type === "summary_result") {
      byId("mediaState").textContent = event.text === "complete"
        ? `整理好了，记下 ${event.value || "0"} 件事`
        : event.text === "minimum_not_met"
          ? "新的聊天还不够多，过一会儿再整理"
          : "这次没整理完，等会儿再试";
      if (state.page === "memory") loadMemories();
    } else if (event.type === "schedule_changed") {
      renderSchedules();
    } else if (event.type === "schedule_fired") {
      byId("mediaState").textContent = event.value || "提醒到点了";
      renderSchedules();
      loadMessages();
    } else if (event.type === "schedule_failed") {
      byId("mediaState").textContent = event.value || "这个提醒没能执行，等会儿会再试";
      renderSchedules();
    }
  }

  byId("enter").addEventListener("click", event => {
    if (!state.enterArmed) {
      state.enterArmed = true;
      event.currentTarget.textContent = "再点一次，进入归巢";
      return;
    }
    api.confirmEnter();
  });
  byId("cancel").addEventListener("click", () => api.cancelEnter());
  byId("return").addEventListener("click", event => {
    if (!state.returnArmed) {
      state.returnArmed = true;
      event.currentTarget.textContent = "再点一次，确认回家";
      return;
    }
    state.returnDismissed = false;
    api.requestFoundationReturn();
  });
  byId("closeReturnPanel").addEventListener("click", () => {
    state.returnDismissed = true;
    renderReturn();
  });
  byId("startReturnSync").addEventListener("click", () => {
    state.returnDismissed = false;
    api.startReturnSync();
  });
  byId("retryReturnSync").addEventListener("click", () => {
    state.returnDismissed = false;
    api.retryReturnSync();
  });
  byId("returnWithoutSync").addEventListener("click", () => {
    if (window.confirm("先不带回去，东西留在手机上，直接回正常模式？")) {
      api.returnWithoutSync();
    }
  });
  byId("abandonHomecoming").addEventListener("click", () => {
    if (window.confirm("真的全部丢掉吗？这次在手机上聊的、记的、定的提醒都会删掉，以家里为准，没法撤销。")) {
      state.returnDismissed = false;
      api.abandonHomecoming();
    }
  });
  byId("peopleTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-timeline]");
    if (button) switchTimeline(button.dataset.timeline);
  });
  byId("memoryTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-memory-owner]");
    if (!button) return;
    state.memoryOwner = button.dataset.memoryOwner;
    loadMemories();
  });
  byId("memorySearch").addEventListener("input", renderMemories);
  document.querySelectorAll("[data-page]").forEach(button => {
    button.addEventListener("click", () => switchPage(button.dataset.page));
  });
  byId("routeSelect").addEventListener("change", () => {
    updateModels();
    saveRoutePreference();
  });
  byId("modelSelect").addEventListener("change", saveRoutePreference);
  byId("settingsOwner").addEventListener("change", () => {
    applyRoutePreference();
    updateModels();
  });
  byId("send").addEventListener("click", send);
  byId("stopReply").addEventListener("click", () => {
    if (state.requestId) api.stopMessage(state.requestId);
  });
  byId("attachToggle").addEventListener("click", () => {
    const menu = byId("attachMenu");
    const opening = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !opening);
    byId("attachToggle").setAttribute("aria-expanded", opening ? "true" : "false");
  });
  byId("pickImage").addEventListener("click", () => { closeAttach(); api.pickImage(); });
  byId("captureImage").addEventListener("click", () => { closeAttach(); api.captureImage(); });
  byId("ttsEnabled").addEventListener("change", event =>
    updateTtsEnabled(event.currentTarget.checked));
  byId("ttsQuickToggle").addEventListener("click", () =>
    updateTtsEnabled(!Boolean(state.bootstrap && state.bootstrap.ttsEnabled)));
  byId("scheduleTab").addEventListener("click", () => {
    byId("scheduleTab").classList.add("active");
    byId("supervisionTab").classList.remove("active");
    byId("schedulePanel").classList.add("active");
    byId("supervisionPanel").classList.remove("active");
    renderSchedules();
  });
  byId("supervisionTab").addEventListener("click", () => {
    byId("supervisionTab").classList.add("active");
    byId("scheduleTab").classList.remove("active");
    byId("supervisionPanel").classList.add("active");
    byId("schedulePanel").classList.remove("active");
    renderSupervisionStatus();
  });
  // 「私聊里」跟着「谁来提醒」走：小克的私聊或 Lucien 的私聊
  function syncSchedulePrivateOption() {
    byId("schedulePrivateOption").value = byId("scheduleOwner").value === "second"
      ? "companion_private" : "main_private";
  }
  byId("scheduleOwner").addEventListener("change", syncSchedulePrivateOption);
  byId("addSchedule").addEventListener("click", () => {
    const content = byId("scheduleContent").value.trim();
    const triggerAt = new Date(byId("scheduleTime").value).getTime();
    if (!content || !Number.isFinite(triggerAt) || triggerAt <= Date.now()) return;
    let created = {};
    try {
      created = JSON.parse(window.HomecomingNative.createSchedule(
        byId("scheduleType").value,
        triggerAt,
        content,
        byId("scheduleOwner").value,
        byId("scheduleTimeline").value));
    } catch (_) {}
    if (created.registrationStatus === "registration_failed") {
      byId("scheduleExactness").textContent =
        "提醒记下了，但手机闹钟没设上；重新打开 App 后会自动再设一次。";
    }
    byId("scheduleContent").value = "";
    renderSchedules();
  });
  byId("summarizeMemories").addEventListener("click", () =>
    window.HomecomingNative.summarizeAllMemories());
  byId("showMemoryCompose").addEventListener("click", () => {
    byId("memoryCompose").classList.remove("hidden");
    byId("memoryText").focus();
  });
  byId("addMemory").addEventListener("click", () => {
    const content = byId("memoryText").value.trim();
    if (!content) return;
    window.HomecomingNative.createMemory(state.memoryOwner, content, "");
    byId("memoryText").value = "";
    byId("memoryCompose").classList.add("hidden");
    loadMemories();
  });
  window.HomecomingPage = { render, onNativeEvent };
  render();
})();
