"use strict";

const TARGET_RATE = 16000;
const FRAME_MS = 100;
const METER_MIN_DB = -70;

const $ = (id) => document.getElementById(id);
const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

// 設定の記憶(ブラウザの保存領域が使えない環境でも動くように try/catch で包む)
const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 保存できなくても動作は続ける */ }
  },
};

// ---------------------------------------------------------------- マイク

let audio = null; // { ctx, stream, node, socket }
let wantRunning = false;

async function listDevices() {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const select = $("device");
  const saved = store.get("deviceId", "");
  select.innerHTML = "";
  for (const d of devices) {
    const option = document.createElement("option");
    option.value = d.deviceId;
    option.textContent = d.label || `マイク ${select.length + 1}`;
    if (d.deviceId === saved) option.selected = true;
    select.appendChild(option);
  }
}

function openAudioSocket() {
  const socket = new WebSocket(wsUrl("/ws/audio"));
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "level") showLevel(msg.dbfs);
  };
  socket.onopen = () => setAudioState("送信中");
  socket.onclose = () => {
    if (!wantRunning || !audio) return;
    setAudioState("再接続中…");
    setTimeout(() => { if (wantRunning && audio) audio.socket = openAudioSocket(); }, 1000);
  };
  return socket;
}

async function start() {
  wantRunning = true;
  $("start").disabled = true;
  const deviceId = $("device").value;
  store.set("deviceId", deviceId);
  const options = { ns: $("opt-ns").checked, ec: $("opt-ec").checked, agc: $("opt-agc").checked };
  store.set("audioOptions", options);
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        noiseSuppression: options.ns,
        echoCancellation: options.ec,
        autoGainControl: options.agc,
      },
    });
    await listDevices(); // 許可後は名前付きで一覧が取れる
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/static/capture-worklet.js");
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "capture", {
      processorOptions: { targetRate: TARGET_RATE, frameMs: FRAME_MS },
    });
    audio = { ctx, stream, node, socket: openAudioSocket() };
    node.port.onmessage = (event) => {
      if (audio && audio.socket.readyState === WebSocket.OPEN) audio.socket.send(event.data);
    };
    source.connect(node);
    // AudioWorklet を動かし続けるには出力先への接続が必要(音は出さない)
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);
    $("stop").disabled = false;
  } catch (err) {
    wantRunning = false;
    $("start").disabled = false;
    setAudioState(`マイクを開けませんでした: ${err.message}`);
  }
}

function stop() {
  wantRunning = false;
  if (audio) {
    audio.socket.close();
    audio.stream.getTracks().forEach((t) => t.stop());
    audio.ctx.close();
    audio = null;
  }
  $("start").disabled = false;
  $("stop").disabled = true;
  setAudioState("停止中");
  showLevel(-120);
}

function setAudioState(text) { $("audio-state").textContent = text; }

function dbToPercent(db) {
  return Math.max(0, Math.min(100, ((db - METER_MIN_DB) / -METER_MIN_DB) * 100));
}

function showLevel(dbfs) {
  $("level").textContent = dbfs <= -119 ? "-- dBFS" : `${dbfs.toFixed(1)} dBFS`;
  $("meter-bar").style.width = `${dbToPercent(dbfs)}%`;
}

// ---------------------------------------------------------------- 設定・状態

async function postSettings(body) {
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  renderStatus(await response.json());
}

function badge(el, cls, text) {
  el.className = `badge ${cls}`;
  el.textContent = text;
}

let firstStatus = true;

function renderStatus(status) {
  $("asr-name").textContent = `認識: ${status.asr}`;
  if (firstStatus) {
    for (const radio of document.querySelectorAll("input[name=mode]")) radio.checked = radio.value === status.mode;
    for (const radio of document.querySelectorAll("input[name=order]")) {
      radio.checked = radio.value === status.bilingual_order;
    }
    $("obs-enabled").checked = status.sinks.obs?.enabled ?? false;
    $("yt-enabled").checked = status.sinks.youtube?.enabled ?? false;
    firstStatus = false;
  }
  if (typeof status.silence_dbfs === "number") $("meter-threshold").style.left = `${dbToPercent(status.silence_dbfs)}%`;

  $("order-row").hidden = status.mode !== "both";
  $("mode-note").textContent = {
    en: "英語の字幕を OBS 経由で YouTube / Twitch の CC として表示します。",
    ja: "日本語は CEA-608 で送れないため、OBS（Twitch）には送られません。YouTube の HTTP POST を使ってください。",
    both: "日本語と英語を2行で表示するのは YouTube の HTTP POST だけです。OBS（CEA-608）には英語だけが送られます。",
  }[status.mode] || "";

  const obs = status.sinks.obs;
  if (obs) {
    if (!obs.enabled) badge($("obs-status"), "", "無効");
    else if (!obs.connected) badge($("obs-status"), "bad", "未接続");
    else if (obs.last_error) badge($("obs-status"), "warn", obs.last_error);
    else badge($("obs-status"), "ok", `接続中 (送信 ${obs.sent})`);
    $("obs-status").title = obs.last_error || obs.url || "";
  }
  const yt = status.sinks.youtube;
  if (yt) {
    if (!yt.enabled) badge($("yt-status"), "", "無効");
    else if (!yt.url) badge($("yt-status"), "bad", "URL 未設定");
    else if (yt.last_error) badge($("yt-status"), "warn", yt.last_error);
    else badge($("yt-status"), "ok", `有効 (送信 ${yt.sent})`);
    $("yt-status").title = yt.last_error || "";
  }
  if ("translator_ready" in status) {
    $("translator-status").textContent = status.translator_ready
      ? "翻訳サーバー: 準備完了"
      : "翻訳サーバー: 未接続（モデルのダウンロード中かもしれません）";
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (response.ok) renderStatus(await response.json());
  } catch { /* サーバー再起動中など */ }
}

// ---------------------------------------------------------------- ログ

function addCaption(c) {
  const row = document.createElement("tr");
  const time = new Date(c.spoken_at * 1000).toLocaleTimeString();
  const cells = [
    ["time", time],
    ["", c.source],
    ["", `${(c.lines || []).map(([, text]) => text).join("\n")}${c.note ? `（${c.note}）` : ""}`],
    ["latency", `${c.total_ms}ms（認識 ${c.asr_ms} / 翻訳 ${c.translate_ms}）`],
  ];
  for (const [cls, text] of cells) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text; // 認識結果は HTML として扱わない
    row.appendChild(td);
  }
  const log = $("log");
  log.prepend(row);
  while (log.children.length > 100) log.lastChild.remove();
}

function openEvents() {
  const socket = new WebSocket(wsUrl("/ws/events"));
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "caption") addCaption(msg.caption);
    else if (msg.type === "history") msg.captions.forEach(addCaption);
    else if (msg.type === "status") renderStatus(msg.status);
    else if (msg.type === "error") setAudioState(msg.message);
  };
  socket.onclose = () => setTimeout(openEvents, 2000);
}

// ---------------------------------------------------------------- 初期化

function init() {
  const options = store.get("audioOptions", { ns: false, ec: false, agc: false });
  $("opt-ns").checked = options.ns;
  $("opt-ec").checked = options.ec;
  $("opt-agc").checked = options.agc;

  $("start").onclick = start;
  $("stop").onclick = stop;
  $("device").onchange = () => { store.set("deviceId", $("device").value); if (audio) { stop(); start(); } };
  for (const radio of document.querySelectorAll("input[name=mode]")) {
    radio.onchange = () => postSettings({ mode: radio.value }).catch((e) => alert(e.message));
  }
  for (const radio of document.querySelectorAll("input[name=order]")) {
    radio.onchange = () => postSettings({ bilingual_order: radio.value }).catch((e) => alert(e.message));
  }
  $("obs-enabled").onchange = () => postSettings({ obs_enabled: $("obs-enabled").checked });
  $("yt-enabled").onchange = () => postSettings({ youtube_enabled: $("yt-enabled").checked });
  $("yt-save").onclick = () => {
    postSettings({ youtube_url: $("yt-url").value }).then(() => { $("yt-url").value = ""; });
  };
  $("test-send").onclick = async () => {
    const text = $("test-text").value.trim();
    if (!text) return;
    await fetch("/api/test-caption", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  };

  if (navigator.mediaDevices?.enumerateDevices) listDevices();
  else setAudioState("このページではマイクを使えません（http://localhost で開いてください）");
  refreshStatus();
  openEvents();
}

init();
