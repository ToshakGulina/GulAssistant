"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  token: localStorage.getItem("gul.token") || "",
  busy: false,
  abort: null,
  providers: [],
  skillsLoaded: false,
  es: null,
};

const messagesEl = $("#messages");
const inputEl = $("#input");
const formEl = $("#form");
const sendEl = $("#send");
const stopEl = $("#stop");
const statusPill = $("#status-pill");

const SOURCE_NAMES = {
  skill: "скилл",
  llm: "нейросеть",
  system: "система",
  wiki: "Википедия",
  mock: "демо",
};

const SKILL_TITLES = {
  stop: "Стоп",
  greeting: "Приветствие",
  help: "Что я умею",
  time: "Время и дата",
  timers: "Таймеры и будильники",
  weather: "Погода",
  news: "Новости",
  currency: "Курсы валют",
  calc: "Калькулятор",
  notes: "Заметки и покупки",
  memory: "Память обо мне",
  wiki: "Википедия",
  music: "Музыка и радио",
  translate: "Перевод",
  smarthome: "Умный дом",
  system: "Управление неттопом",
};

function authHeaders(extra = {}) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (state.token) headers["X-Gul-Token"] = state.token;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  if (response.status === 401) {
    showTokenModal();
    throw new Error("unauthorized");
  }
  return response;
}

/* ---------------- сообщения ---------------- */

function scrollDown() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function addMessage(role, text = "", options = {}) {
  const wrap = document.createElement("article");
  wrap.className = `msg ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "Я" : "Г";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const textEl = document.createElement("div");
  textEl.className = "text";
  const metaEl = document.createElement("div");
  metaEl.className = "msg-meta";

  bubble.append(textEl, metaEl);
  wrap.append(avatar, bubble);
  messagesEl.append(wrap);

  if (options.typing) {
    setTyping(textEl);
  } else {
    textEl.textContent = text;
  }
  scrollDown();
  return { wrap, bubble, textEl, metaEl, typing: !!options.typing, raw: text };
}

function setTyping(element) {
  element.innerHTML = '<span class="typing" aria-label="Гуль думает"><i></i><i></i><i></i></span>';
}

function clearTyping(context) {
  if (context.typing) {
    context.textEl.textContent = "";
    context.typing = false;
  }
}

function setBadge(context, label, kind = "") {
  if (!label) return;
  const badge = document.createElement("span");
  badge.className = `badge ${kind}`.trim();
  badge.textContent = label;
  context.metaEl.append(badge);
}

function setLatency(context, ms, title = "") {
  if (!(ms > 0)) return;
  const chip = document.createElement("span");
  chip.className = `latency ${ms <= 5000 ? "good" : "slow"}`;
  chip.textContent = ms < 950 ? `⚡ ${Math.round(ms)} мс` : `⚡ ${(ms / 1000).toFixed(1)} с`;
  if (title) chip.title = title;
  context.metaEl.append(chip);
}

function setMeta(context, event) {
  context.metaEl.textContent = "";
  let kind = "llm";
  let label = "нейросеть";
  if (event.source === "skill") {
    kind = "";
    label = `скилл · ${SKILL_TITLES[event.skill] || event.skill || "команда"}`;
  } else if (event.source === "llm") {
    const provider = state.providers.find((p) => p.name === event.provider);
    if (event.provider === "mock") {
      kind = "demo";
      label = "демо-мозг";
    } else {
      label = provider ? provider.title : "нейросеть";
      if (!provider) kind = "llm";
    }
  } else if (event.source === "wiki") {
    kind = "";
    label = "Википедия";
  } else {
    kind = "";
    label = SOURCE_NAMES[event.source] || "Гуль";
  }
  if (event.error) {
    kind = "error";
    label = "ошибка сети";
  }
  setBadge(context, label, kind);
  const title = event.first_token_ms
    ? `Первый токен: ${(event.first_token_ms / 1000).toFixed(2)} с · всего: ${(event.total_ms / 1000).toFixed(2)} с`
    : `Всего: ${((event.total_ms || 0) / 1000).toFixed(2)} с`;
  setLatency(context, event.first_token_ms || event.total_ms, title);
}

function handleStreamEvent(event, context) {
  if (event.type === "state") {
    if (event.state === "thinking" && !context.typing) {
      setTyping(context.textEl);
      context.typing = true;
    }
    return;
  }
  if (event.type === "delta") {
    if (context.typing) {
      context.textEl.textContent = "";
      context.typing = false;
    }
    context.raw += event.text || "";
    context.textEl.textContent = context.raw;
    scrollDown();
    return;
  }
  if (event.type === "skill") {
    clearTyping(context);
    context.raw = event.text || "";
    context.textEl.textContent = context.raw;
    scrollDown();
    return;
  }
  if (event.type === "done") {
    clearTyping(context);
    if (event.text) {
      context.raw = event.text;
      context.textEl.textContent = event.text;
    }
    setMeta(context, event);
    scrollDown();
    return;
  }
  if (event.type === "error") {
    clearTyping(context);
    context.textEl.textContent = event.text || "Что-то пошло не так.";
  }
}

/* ---------------- отправка ---------------- */

async function sendMessage(text) {
  const value = (text ?? inputEl.value).trim();
  if (!value || state.busy) return;

  if (!text) {
    inputEl.value = "";
    resizeInput();
  }
  addMessage("user", value);
  state.busy = true;
  stopEl.classList.remove("hidden");
  sendEl.disabled = true;
  const context = addMessage("assistant", "", { typing: true });
  state.abort = new AbortController();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ text: value }),
      signal: state.abort.signal,
    });
    if (!response.ok) {
      let hint = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        hint = data.hint || data.error || hint;
      } catch (_) {}
      throw new Error(hint);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value: chunk, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(chunk, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (!line) continue;
        try {
          handleStreamEvent(JSON.parse(line), context);
        } catch (error) {
          console.warn("Некорректное событие", line, error);
        }
      }
    }
    if (!context.metaEl.children.length) {
      setMeta(context, { source: "llm", total_ms: 0 });
    }
  } catch (error) {
    clearTyping(context);
    if (error.name === "AbortError") {
      context.textEl.textContent = context.raw || "Остановлено.";
      setBadge(context, "остановлено", "");
    } else if (error.message !== "unauthorized") {
      context.textEl.textContent = `Не удалось получить ответ: ${error.message}`;
      setBadge(context, "ошибка", "error");
    }
  } finally {
    state.busy = false;
    state.abort = null;
    stopEl.classList.add("hidden");
    sendEl.disabled = false;
    scrollDown();
    loadStatus();
  }
}

function resizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(140, inputEl.scrollHeight)}px`;
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

inputEl.addEventListener("input", resizeInput);
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

$$("#suggestions button").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.text || button.textContent));
});

stopEl.addEventListener("click", async () => {
  try {
    if (state.abort) state.abort.abort();
    await api("/api/stop", { method: "POST", body: "{}" });
    toast("Остановлено", "⏹");
  } catch (_) {}
});

$("#clear").addEventListener("click", async () => {
  try {
    await api("/api/clear", { method: "POST", body: "{}" });
  } catch (_) {}
  messagesEl.textContent = "";
  welcome();
});

/* ---------------- статус ---------------- */

function providerSubtitle(provider) {
  if (!provider.available) return "нет ключа";
  if (provider.cooldown_seconds > 0) return `пауза ${provider.cooldown_seconds} с`;
  if (provider.state === "error") return "ошибка · резерв";
  if (provider.last_latency_ms) return `первый токен ${(provider.last_latency_ms / 1000).toFixed(1)} с`;
  return "готов";
}

function renderProviders(providers) {
  const list = $("#providers");
  list.textContent = "";
  providers.forEach((provider) => {
    const row = document.createElement("div");
    row.className = `provider${provider.active ? " active" : ""}`;

    const dot = document.createElement("span");
    dot.className = "dot";
    if (!provider.available) dot.classList.add("off");
    else if (provider.cooldown_seconds > 0) dot.classList.add("cooldown");
    else if (provider.state === "error") dot.classList.add("error");
    else dot.classList.add("ok");

    const main = document.createElement("div");
    main.className = "p-main";
    const title = document.createElement("div");
    title.className = "p-title";
    title.textContent = provider.title;
    const sub = document.createElement("div");
    sub.className = "p-sub";
    sub.textContent = providerSubtitle(provider);
    main.append(title, sub);

    const latency = document.createElement("div");
    latency.className = "p-lat";
    latency.textContent = provider.calls ? `${provider.calls}×` : "";
    latency.title = provider.last_error || "";

    row.append(dot, main, latency);
    list.append(row);
  });
}

function shortenCpu(model) {
  return (model || "—")
    .replace(/\(R\)|\(TM\)|CPU|Processor/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function renderSkills(skills) {
  const list = $("#skills");
  list.textContent = "";
  const items = skills
    .filter((skill) => skill.name !== "stop" && skill.name !== "system")
    .sort((a, b) => (SKILL_TITLES[a.name] ? 0 : 1) - (SKILL_TITLES[b.name] ? 0 : 1));
  items.forEach((skill) => {
    const item = document.createElement("div");
    item.className = "skill-item";
    const title = document.createElement("div");
    title.className = "s-title";
    title.textContent = SKILL_TITLES[skill.name] || skill.title;
    const desc = document.createElement("div");
    desc.className = "s-desc";
    desc.textContent = skill.examples && skill.examples.length
      ? `«${skill.examples[0]}»`
      : skill.description;
    item.append(title, desc);
    list.append(item);
  });
  $("#skills-count").textContent = `${skills.length} команд`;
}

function renderStatus(data) {
  $("#uptime").textContent = `аптайм ${data.uptime}`;
  $("#uptime-2").textContent = data.uptime;
  $("#cpu").textContent = shortenCpu(data.cpu && data.cpu.model);
  const memory = data.memory || {};
  if (memory.total) {
    $("#mem-text").textContent = `${((memory.total - memory.available) / 1024).toFixed(1)} / ${(memory.total / 1024).toFixed(1)} ГБ`;
    $("#mem-bar").style.width = `${memory.used_percent || 0}%`;
  }
  const tts = (data.tts || []).filter((engine) => engine.available).map((engine) => engine.title.split(" ")[0]);
  const audioBits = [];
  if (data.audio && data.audio.player) audioBits.push("mpv");
  if (tts.length) audioBits.push(tts[0]);
  $("#audio-state").textContent = audioBits.join(" · ") || "без звука";

  state.providers = data.providers || [];
  renderProviders(state.providers);
  if (!state.skillsLoaded) {
    renderSkills(data.skills || []);
    state.skillsLoaded = true;
  }

  const latency = (data.metrics && data.metrics.latency) || {};
  const count = latency.count || 0;
  $("#p50").textContent = latency.p50_ms != null ? latency.p50_ms : "—";
  $("#p95").textContent = latency.p95_ms != null ? `${(latency.p95_ms / 1000).toFixed(1)} с` : "—";
  $("#answers").textContent = count;
  $("#under5").textContent = count ? `${latency.under_5s_percent}%` : "пока нет данных";
  $("#under5-bar").style.width = count ? `${latency.under_5s_percent}%` : "0%";

  const realProviders = state.providers.filter((p) => p.available && p.name !== "mock");
  const demo = realProviders.length === 0;
  statusPill.classList.toggle("demo", demo);
  statusPill.querySelector("span").textContent = demo ? "демо-режим" : "онлайн";
  $("#demo-note").textContent = demo ? "Подключите GigaChat в .env — станет умнее" : "";

  const warnings = data.warnings || [];
  const warnBox = $("#warnings");
  if (warnings.length) {
    warnBox.hidden = false;
    warnBox.textContent = warnings[0];
  } else {
    warnBox.hidden = true;
  }
}

async function loadStatus() {
  try {
    const response = await api("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderStatus(await response.json());
    if (statusPill.classList.contains("offline")) {
      statusPill.classList.remove("offline");
    }
  } catch (error) {
    statusPill.classList.add("offline");
    statusPill.querySelector("span").textContent = "нет связи";
  }
}

/* ---------------- события и мелочи ---------------- */

function toast(text, icon = "💬", kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`.trim();
  const iconEl = document.createElement("div");
  iconEl.className = "t-icon";
  iconEl.textContent = icon;
  const textEl = document.createElement("div");
  textEl.className = "t-text";
  textEl.textContent = text;
  node.append(iconEl, textEl);
  $("#toasts").append(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    node.style.transition = "all .3s ease";
    setTimeout(() => node.remove(), 320);
  }, 5200);
}

function connectEvents() {
  if (state.es) state.es.close();
  const query = state.token ? `?token=${encodeURIComponent(state.token)}` : "";
  const es = new EventSource(`/api/events${query}`);
  state.es = es;
  es.onmessage = (message) => {
    try {
      const payload = JSON.parse(message.data);
      if (payload.event === "timer_fired") {
        toast(payload.data.message, "⏰");
      }
    } catch (_) {}
  };
  es.onerror = () => {};
}

function showTokenModal() {
  $("#token-modal").classList.remove("hidden");
  $("#token-input").focus();
}

$("#token-save").addEventListener("click", () => {
  const value = $("#token-input").value.trim();
  if (value) localStorage.setItem("gul.token", value);
  state.token = value;
  location.reload();
});

$("#voice-test").addEventListener("click", async () => {
  try {
    const response = await api("/api/speak", {
      method: "POST",
      body: JSON.stringify({ text: "Привет! Я Гуль, ваш голосовой ассистент." }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 503) {
      toast(data.hint || "Синтез речи не настроен", "⚠️", "error");
    } else if (!response.ok) {
      toast(data.error || "Не удалось озвучить", "⚠️", "error");
    } else {
      toast("Фраза отправлена на озвучку", "🔊");
    }
  } catch (_) {}
});

function welcome() {
  const context = addMessage(
    "assistant",
    "Привет! Я Гуль — голосовой ассистент, который живёт на вашем железе.\n" +
      "Спросите погоду, время или курс доллара, поставьте таймер, включите радио — " +
      "а на сложные вопросы отвечу с помощью нейросети. Всё бесплатно."
  );
  setBadge(context, "Гуль", "");
}

/* ---------------- старт ---------------- */

welcome();
loadStatus();
connectEvents();
setInterval(loadStatus, 5000);
resizeInput();
if (state.token) {
  // Токен уже сохранён — модалку не показываем, но проверка произойдёт при запросах.
}
