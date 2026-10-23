"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const player = $("#player");

const COLORS = {
  "Goal": "var(--c-goal)", "Aerial": "var(--c-aerial)", "Save": "var(--c-save)", "Demo": "var(--c-demo)",
  "Assist": "var(--c-assist)", "Funny Moment": "var(--c-funny)", "Fail?": "var(--c-fail)", "Manual": "var(--c-manual)",
};

const state = {
  videos: [], jobs: [], music: [], categories: [], format: "vertical",
  current: null, clips: [], source: null, sel: null, win: null, stopAt: null,
  hidden: new Set(load("hiddenCats", [])), showRemoved: false, sort: "time", replays: [], replaySel: null,
};
const jobStatus = new Map();

function load(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ }
}

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
const report = err => { console.error(err); alert(err.message || String(err)); };
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

function fmtTime(sec) {
  sec = Math.max(0, sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(s).padStart(2, "0")}`;
}
const fmtRel = t => (t >= 0 ? "+" : "−") + Math.abs(t).toFixed(1) + "s";
const selClip = () => state.clips.find(c => c.clip === state.sel);
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

// ------------------------------------------------------------------ app state + tasks

async function refreshState() {
  let data;
  try { data = await api("/api/state"); } catch { return; }
  Object.assign(state, { videos: data.videos, replays: data.replays || [], jobs: data.jobs, music: data.music,
                         categories: data.categories, format: data.format, version: data.version });
  renderRecordings();
  renderStatus();
  renderUpdate(data.update);
  for (const job of data.jobs) {
    const before = jobStatus.get(job.id);
    jobStatus.set(job.id, job.status);
    if (before === undefined || before === job.status) continue;
    if (job.status === "done" || job.status === "error") {
      if (job.video === state.current) {
        if (job.kind === "analyze") loadClips(true);
        else softRefresh();
      }
      if (job.kind === "compile" && job.status === "done" && job.result) openModal(job.result);
      if (job.kind === "update" && job.status === "done" && job.result === "restart") {
        $("#restarting").hidden = false;
        api("/api/restart", {}).catch(() => {});
      }
      if (job.status === "error" && job.kind !== "update") renderStatus();
    }
  }
}

function renderRecordings() {
  const sel = $("#recording");
  const key = state.videos.map(v => v.name).join("|") + "#" + state.replays.map(r => r.path).join("|");
  if (sel.dataset.key !== key) {
    sel.dataset.key = key;
    sel.innerHTML = "";
    for (const v of state.videos) sel.add(new Option("", v.name));
    if (state.replays.length) {
      const group = document.createElement("optgroup");
      group.label = "Replays";
      for (const r of state.replays) group.appendChild(new Option("", "replay:" + r.path));
      sel.appendChild(group);
    }
  }
  for (const o of sel.options) {
    if (o.value.startsWith("replay:")) {
      const r = state.replays.find(x => "replay:" + x.path === o.value);
      o.textContent = `Replay: ${r.name || r.file}${r.date ? " \u00b7 " + r.date : ""}  \u2014  ` +
        (r.recording ? `paired with ${r.recording}` : r.date ? "needs its recording" : "reading...");
    } else {
      const v = state.videos.find(x => x.name === o.value);
      o.textContent = `${v.name}  \u2014  ${v.scanned ? `${v.kept} kept of ${v.clips}` : "not analyzed"}`;
    }
  }
  const any = state.videos.length + state.replays.length > 0;
  sel.value = state.replaySel ? "replay:" + state.replaySel : (state.current || "");
  $("#welcome").hidden = any;
  $("#replay-view").hidden = !state.replaySel;
  $("#studio").hidden = !any || Boolean(state.replaySel) || state.videos.length === 0;
  $("#recording").hidden = $("#remove").hidden = !any;
  $("#analyze").hidden = $("#speed").hidden = state.videos.length === 0 || Boolean(state.replaySel);
  if (!state.current && !state.replaySel && state.videos.length) selectRecording(state.videos[0].name);
  if (!state.current && !state.replaySel && !state.videos.length && state.replays.length) showReplay(state.replays[0].path);
  if (state.replaySel) renderReplay();
  const v = state.videos.find(x => x.name === state.current);
  const busy = busyJob(state.current, "analyze");
  $("#analyze").textContent = busy ? "Analyzing..." : v?.scanned ? "Re-analyze" : "Analyze";
  $("#analyze").disabled = Boolean(busy);
}

function showReplay(path) {
  state.replaySel = path;
  player.pause();
  renderRecordings();
}

const fmtClock = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function renderReplay() {
  const r = state.replays.find(x => x.path === state.replaySel);
  if (!r) { state.replaySel = null; return; }
  $("#rv-title").textContent = r.name || r.file;
  $("#rv-sub").textContent = r.date ? `Match started ${r.date} \u00b7 ${r.result || ""}` : "Reading the replay...";
  const st = r.stats;
  $("#rv-stats").innerHTML = "";
  if (st) {
    for (const [k, label] of [["Goals", "goal"], ["Assists", "assist"], ["Saves", "save"], ["Shots", "shot"], ["Score", "score"]]) {
      const sp = document.createElement("span");
      const n = st[k] ?? 0;
      sp.textContent = `${n} ${label}${n === 1 || label === "score" ? "" : "s"}`;
      $("#rv-stats").appendChild(sp);
    }
  }
  const pair = $("#rv-pair");
  pair.className = "rv-pair " + (r.recording ? "ok" : "missing");
  pair.textContent = r.recording ? `\u2714 Paired with ${r.recording}`
    : r.date ? `No recording paired yet. Import or choose the video you recorded on ${r.date}.` : "";
  $("#rv-open").hidden = !r.recording || !state.videos.some(v => v.name === r.recording);
  $("#rv-choose").textContent = r.recording ? "Choose a different recording" : "Choose the recording of this match";
  const ul = $("#rv-timeline");
  ul.innerHTML = "";
  for (const e of r.timeline || []) {
    const li = document.createElement("li");
    li.className = e.mine ? "mine" : "";
    li.innerHTML = "<span></span><span></span><span></span><span></span>";
    const [a, b, c, d] = li.children;
    a.textContent = fmtClock(e.time);
    b.textContent = e.mine && e.label === "Goal" ? "Your goal" : e.label;
    c.textContent = e.mine ? "" : e.player;
    d.textContent = e.stat;
    ul.appendChild(li);
  }
  if (!(r.timeline || []).length) ul.innerHTML = `<li><span></span><span class="muted">${r.date ? "No goals or plays recorded" : "Reading..."}</span></li>`;
}

function busyJob(name, kind) {
  return state.jobs.find(j => j.video === name && (!kind || j.kind === kind) &&
                              (j.status === "queued" || j.status === "running"));
}

function renderStatus() {
  const running = state.jobs.find(j => j.status === "running");
  $("#cancel").hidden = !running || running.kind === "update";
  $("#cancel").disabled = Boolean(running?.cancel);
  const queued = state.jobs.filter(j => j.status === "queued").length;
  const lastErr = [...state.jobs].reverse().find(j => j.status === "error");
  const btn = $("#status");
  btn.classList.toggle("error", !running && Boolean(lastErr) && lastErr === state.jobs.at(-1));
  if (running) {
    $(".status-text", btn).textContent = running.message + (queued ? ` (+${queued} waiting)` : "");
    $(".status-fill", btn).style.width = `${running.progress}%`;
  } else {
    const last = state.jobs.at(-1);
    $(".status-text", btn).textContent = last ? (last.status === "error" ? "Failed: " : "") + last.message : "Ready";
    $(".status-fill", btn).style.width = last && last.status === "done" ? "100%" : "0%";
  }
  if (!$("#tasks").hidden) renderTasks();
}

function renderTasks() {
  const box = $("#task-list");
  box.innerHTML = state.jobs.length ? "" : `<div class="muted">No tasks yet.</div>`;
  for (const job of [...state.jobs].reverse()) {
    const el = document.createElement("div");
    el.className = `task ${job.status}`;
    el.innerHTML = `<div class="task-row"><div><b></b> \u00b7 <span class="m"></span></div></div>
      <div class="bar"><div class="fill"></div></div>`;
    $("b", el).textContent = `${job.kind[0].toUpperCase()}${job.kind.slice(1)} ${job.video}`;
    $(".m", el).textContent = job.status === "queued" ? "waiting" : job.message;
    $(".fill", el).style.width = `${job.progress}%`;
    if (job.status === "queued" || job.status === "running") {
      const b = document.createElement("button");
      b.textContent = job.status === "queued" ? "Remove" : "Cancel";
      b.onclick = ev => { ev.stopPropagation(); cancelJob(job.id); };
      $(".task-row", el).appendChild(b);
    }
    if (job.result && job.result !== "restart") {
      const a = document.createElement("a");
      a.href = "#";
      a.textContent = " Watch";
      a.onclick = ev => { ev.preventDefault(); openModal(job.result); };
      $(".m", el).after(a);
    }
    box.appendChild(el);
  }
  $("#version").textContent = `Clip Studio ${state.version || ""}`;
}

async function cancelJob(id) {
  try { await api("/api/cancel", { job: id }); refreshState(); } catch (e) { report(e); }
}

function renderUpdate(update) {
  const btn = $("#update");
  btn.hidden = !update?.available;
  if (update?.available) btn.textContent = `\u2B06 Update to ${update.latest}`;
}

// ------------------------------------------------------------------ recordings + clips

async function selectRecording(name) {
  const fromReplay = Boolean(state.replaySel);
  state.replaySel = null;
  if (state.current === name) { if (fromReplay) renderRecordings(); return; }
  state.current = name;
  state.sel = null;
  $("#recording").value = name;
  await loadClips(true);
}

async function loadClips(resetSource) {
  if (!state.current) return;
  let data;
  try { data = await api(`/api/clips?video=${encodeURIComponent(state.current)}`); }
  catch (e) { return report(e); }
  state.clips = data.clips;
  renderWarnings(data.warnings, data.notes);
  if (resetSource || state.source !== data.source) {
    state.source = data.source;
    $("#player-msg").hidden = true;
    player.src = data.source;
  }
  if (!selClip()) state.sel = visibleClips()[0]?.clip ?? null;
  renderAll();
  if (state.sel !== null) seekTo(selClip().start);
}

async function softRefresh() {
  let data;
  try { data = await api(`/api/clips?video=${encodeURIComponent(state.current)}`); } catch { return; }
  const byId = new Map(data.clips.map(c => [c.clip, c]));
  for (const c of state.clips) if (byId.has(c.clip)) c.rendered = byId.get(c.clip).rendered;
  renderList();
  renderEditor();
}

function visibleClips() {
  const list = state.clips.filter(c => !state.hidden.has(c.category) && (state.showRemoved || !c.skip));
  return state.sort === "score"
    ? list.sort((a, b) => (parseFloat(b.score) || 0) - (parseFloat(a.score) || 0))
    : list.sort((a, b) => a.anchor - b.anchor);
}

function renderWarnings(list, notes) {
  const box = $("#warnings");
  box.innerHTML = "";
  for (const w of list || []) {
    const p = document.createElement("p");
    p.textContent = "\u26A0 " + w;
    box.appendChild(p);
  }
  box.hidden = !(list && list.length);
  const nbox = $("#notes");
  nbox.innerHTML = "";
  for (const n of notes || []) {
    const p = document.createElement("p");
    p.textContent = (n.startsWith("Used replay") ? "\u2714 " : "\u2139 ") + n;
    nbox.appendChild(p);
  }
  nbox.hidden = !(notes && notes.length);
}

function renderAll() {
  renderChips();
  renderList();
  renderEditor();
  renderTimeline();
  renderOverview();
}

function renderChips() {
  const box = $("#chips");
  box.innerHTML = "";
  for (const cat of state.categories) {
    const n = state.clips.filter(c => c.category === cat && (state.showRemoved || !c.skip)).length;
    const b = document.createElement("button");
    b.className = "chip" + (state.hidden.has(cat) ? " off" : "");
    b.innerHTML = `<span class="dot"></span><span></span>`;
    $(".dot", b).style.background = COLORS[cat];
    $("span:last-child", b).textContent = `${cat} ${n}`;
    b.onclick = () => {
      state.hidden.has(cat) ? state.hidden.delete(cat) : state.hidden.add(cat);
      store("hiddenCats", [...state.hidden]);
      renderAll();
    };
    box.appendChild(b);
  }
}

function renderList() {
  const ul = $("#cliplist");
  ul.innerHTML = "";
  const list = visibleClips();
  if (!list.length) {
    const v = state.videos.find(x => x.name === state.current);
    ul.innerHTML = `<li class="muted">${busyJob(state.current, "analyze") ? "Analyzing... clips appear here when it's done."
      : v?.scanned ? "No clips match. Turn categories back on above." : "Click Analyze to find clips."}</li>`;
  }
  for (const c of list) {
    const li = document.createElement("li");
    li.className = (c.clip === state.sel ? "sel " : "") + (c.skip ? "removed" : "");
    li.innerHTML = `<span class="dot"></span><span class="t"></span><span class="meta"></span><span class="why"></span>`;
    $(".dot", li).style.background = COLORS[c.category] || "var(--muted)";
    $(".t", li).textContent = c.label === "HIGHLIGHT" ? "Manual clip" : titleCase(c.label);
    $(".meta", li).textContent = `${fmtTime(c.anchor)} · ${(c.end - c.start).toFixed(0)}s`;
    $(".why", li).textContent = (c.rendered ? "✓ exported · " : "") + (c.reason || "");
    li.onclick = () => selectClip(c.clip);
    ul.appendChild(li);
  }
  const kept = state.clips.filter(c => !c.skip).length;
  $("#export-kept").textContent = `Export kept clips (${kept})`;
  $("#export-kept").disabled = $("#compile").disabled = kept === 0;
}

const titleCase = s => s.toLowerCase().replace(/\b\w/g, ch => ch.toUpperCase());

function selectClip(n) {
  state.sel = n;
  state.win = null;
  state.stopAt = null;
  renderList();
  renderEditor();
  renderTimeline();
  renderOverview();
  const c = selClip();
  if (c) seekTo(c.start);
}

function seekTo(t) {
  if (!isFinite(t)) return;
  if (player.readyState >= 1) player.currentTime = t;
  else player.addEventListener("loadedmetadata", () => { player.currentTime = t; }, { once: true });
}

// ------------------------------------------------------------------ editor

function renderEditor() {
  const c = selClip();
  $("#editor").style.visibility = c ? "visible" : "hidden";
  if (!c) return;
  $("#ed-title").textContent = c.label === "HIGHLIGHT" ? "Manual clip" : titleCase(c.label);
  $("#ed-time").textContent = `At ${fmtTime(c.anchor)} in the recording`;
  const cat = $("#ed-category");
  if (cat.options.length !== state.categories.length) {
    cat.innerHTML = "";
    for (const x of state.categories) cat.add(new Option(x, x));
  }
  cat.value = c.category;
  $("#ed-reason").textContent = c.reason ? `Why: ${c.reason}` : "";
  const keep = $("#ed-keep");
  keep.textContent = c.skip ? "Removed — click to keep" : "Kept — click to remove";
  keep.className = "keep " + (c.skip ? "removed" : "kept");
  $("#ed-start").textContent = fmtRel(c.start - c.anchor);
  $("#ed-end").textContent = fmtRel(c.end - c.anchor);
  $("#ed-length").textContent = `${(c.end - c.start).toFixed(1)} seconds (${fmtTime(c.start)} – ${fmtTime(c.end)})`;
  if (document.activeElement !== $("#ed-caption")) $("#ed-caption").value = c.caption || "";

  const music = $("#ed-music");
  const wanted = ["", ...state.music].join("|");
  if (music.dataset.list !== wanted) {
    music.dataset.list = wanted;
    music.innerHTML = `<option value="">Auto (matches the play)</option><option value="none">No music (add a trending sound in TikTok/YouTube)</option>`;
    for (const m of state.music) {
      const o = document.createElement("option");
      o.value = m.split("/").pop();
      o.textContent = m;
      music.appendChild(o);
    }
    if (!state.music.length) music.innerHTML = `<option value="">No music yet — add songs to the music folder</option><option value="none">No music (add a trending sound in TikTok/YouTube)</option>`;
  }
  music.value = c.music || "";
  $("#ed-game").value = c.game_volume;
  $("#ed-game-v").textContent = `${Math.round(c.game_volume)}%`;
  $("#ed-musicvol").value = c.music_volume;
  $("#ed-musicvol-v").textContent = `${Math.round(c.music_volume)}%`;
  for (const b of $$("#ed-framing button")) b.classList.toggle("active", b.dataset.framing === c.framing);
  $("#crop-row").hidden = c.framing !== "crop" || state.format !== "vertical";
  $("#ed-framing").hidden = state.format !== "vertical";
  $("#ed-cropx").value = c.crop_x;
  $("#crop-row span").textContent = c.crop_x < 0.4 ? "Left" : c.crop_x > 0.6 ? "Right" : "Center";
  const job = busyJob(state.current, "export");
  $("#ed-export").textContent = c.rendered ? "Re-export this clip" : "Export this clip";
  $("#ed-export").disabled = c.skip;
  $("#ed-watch").hidden = !c.rendered;
  if (job) $("#ed-export").textContent = "Exporting...";
}

const pending = new Map();
function save(fields, clip = selClip()) {
  if (!clip) return;
  Object.assign(clip, fields);
  const key = clip.clip;
  pending.set(key, { ...(pending.get(key) || {}), ...fields });
  flush(key, clip);
}
const flush = debounce(async (key) => {
  const fields = pending.get(key);
  pending.delete(key);
  if (!fields) return;
  try { await api("/api/clip", { video: state.current, clip: key, fields }); }
  catch (e) { report(e); }
}, 350);

function setTrim(which, t) {
  const c = selClip();
  if (!c) return;
  const dur = player.duration || Infinity;
  if (which === "start") c.start = Math.min(Math.max(0, t), c.end - 1);
  else c.end = Math.max(Math.min(dur, t), c.start + 1);
  c.start = Math.round(c.start * 100) / 100;
  c.end = Math.round(c.end * 100) / 100;
  save({ start: c.start, end: c.end });
  renderEditor();
  renderTimeline();
  renderOverview();
}

// ------------------------------------------------------------------ timelines

function windowFor(c) {
  const dur = player.duration || c.end + 20;
  let w = state.win;
  if (!w || c.start < w[0] || c.end > w[1]) {
    w = [Math.max(0, c.start - 15), Math.min(dur, c.end + 15)];
    if (w[1] - w[0] < 20) w[1] = Math.min(dur, w[0] + 20);
  }
  state.win = w;
  return w;
}

function renderTimeline() {
  const c = selClip();
  const box = $("#detail");
  box.style.visibility = c ? "visible" : "hidden";
  if (!c) return;
  const [w0, w1] = windowFor(c);
  const pct = t => `${((t - w0) / (w1 - w0)) * 100}%`;
  $(".d-range", box).style.left = pct(c.start);
  $(".d-range", box).style.width = `${((c.end - c.start) / (w1 - w0)) * 100}%`;
  $(".d-handle.start", box).style.left = pct(c.start);
  $(".d-handle.end", box).style.left = pct(c.end);
  $(".d-anchor", box).style.left = pct(c.anchor);
  const ticks = $(".d-ticks", box);
  ticks.innerHTML = "";
  for (let s = Math.ceil((w0 - c.anchor) / 5) * 5; c.anchor + s <= w1; s += 5) {
    const sp = document.createElement("span");
    sp.style.left = pct(c.anchor + s);
    sp.textContent = s === 0 ? "play" : `${s > 0 ? "+" : ""}${s}s`;
    ticks.appendChild(sp);
  }
  drawPlayheads();
}

function renderOverview() {
  const box = $("#overview");
  $$(".o-mark", box).forEach(m => m.remove());
  const dur = player.duration;
  if (!dur) return;
  for (const c of state.clips) {
    if (state.hidden.has(c.category)) continue;
    const m = document.createElement("div");
    m.className = "o-mark" + (c.skip ? " removed" : "") + (c.clip === state.sel ? " sel" : "");
    m.style.left = `${(c.start / dur) * 100}%`;
    m.style.width = `${((c.end - c.start) / dur) * 100}%`;
    m.style.background = COLORS[c.category];
    m.title = `${c.label} at ${fmtTime(c.anchor)}`;
    m.onclick = ev => { ev.stopPropagation(); selectClip(c.clip); };
    box.appendChild(m);
  }
}

function drawPlayheads() {
  const t = player.currentTime;
  if (state.win) {
    const [w0, w1] = state.win;
    const inside = t >= w0 && t <= w1;
    $(".d-playhead").style.display = inside ? "block" : "none";
    $(".d-playhead").style.left = `${((t - w0) / (w1 - w0)) * 100}%`;
  }
  if (player.duration) $(".o-playhead").style.left = `${(t / player.duration) * 100}%`;
  $("#clock").textContent = `${fmtTime(t)} / ${fmtTime(player.duration)}`;
}

function timeAt(ev, el, w0, w1) {
  const r = el.getBoundingClientRect();
  return w0 + Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * (w1 - w0);
}

$("#detail").addEventListener("pointerdown", ev => {
  const c = selClip();
  if (!c || !state.win) return;
  const [w0, w1] = state.win;
  const handle = ev.target.closest(".d-handle");
  if (!handle) { seekTo(timeAt(ev, $("#detail"), w0, w1)); return; }
  const which = handle.classList.contains("start") ? "start" : "end";
  const el = $("#detail");
  el.setPointerCapture(ev.pointerId);
  player.pause();
  const move = e => {
    const t = timeAt(e, el, w0, w1);
    setTrim(which, t);
    seekTo(which === "start" ? c.start : c.end);
  };
  const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); };
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
});

$("#overview").addEventListener("pointerdown", ev => {
  if (!player.duration || ev.target.classList.contains("o-mark")) return;
  seekTo(timeAt(ev, $("#overview"), 0, player.duration));
});

// ------------------------------------------------------------------ player

player.addEventListener("loadedmetadata", () => { renderOverview(); renderTimeline(); });
player.addEventListener("timeupdate", () => {
  drawPlayheads();
  if (state.stopAt !== null && player.currentTime >= state.stopAt) {
    player.pause();
    state.stopAt = null;
  }
});
player.addEventListener("pause", () => { $("#playpause").innerHTML = "&#9654;"; });
player.addEventListener("play", () => { $("#playpause").innerHTML = "&#10073;&#10073;"; });
player.addEventListener("error", () => {
  if (!player.getAttribute("src")) return;
  const msg = $("#player-msg");
  msg.hidden = false;
  msg.textContent = "This recording can't be played here (MKV files usually can't). Analysis and export " +
    "still work. To preview it, remux it to MP4 in OBS (File → Remux Recordings) and import that.";
});
setInterval(drawPlayheads, 250);

function playClip() {
  const c = selClip();
  if (!c) return;
  player.currentTime = c.start;
  state.stopAt = c.end;
  player.play();
}

function step(dir) {
  const list = visibleClips();
  const i = list.findIndex(c => c.clip === state.sel);
  const next = list[Math.min(list.length - 1, Math.max(0, i + dir))];
  if (next) selectClip(next.clip);
}

// ------------------------------------------------------------------ import

async function importPaths(paths) {
  if (!paths || !paths.length) return;
  try {
    const res = await api("/api/import", { paths });
    if (paths.every(p => p.toLowerCase().endsWith(".replay"))) {
      await refreshState();
      const added = state.replays.find(r => paths.some(p => p.endsWith(r.file)));
      if (added) showReplay(added.path);
      return;
    }
    await window.onImported(res.added);
  } catch (e) { report(e); }
}

window.onImported = async names => {
  await refreshState();
  // Replays aren't recordings: they're matched in the background (see the task list).
  const replay = state.replays.find(r => (names || []).includes(r.file));
  names = (names || []).filter(n => state.videos.some(v => v.name === n));
  if (!names.length) { if (replay) showReplay(replay.path); return; }
  await selectRecording(names[0]);
  for (const name of names) {
    const v = state.videos.find(x => x.name === name);
    if (v && !v.scanned && !busyJob(name)) await api("/api/analyze", { video: name, speed: $("#speed").value }).catch(report);
  }
  refreshState();
};

async function browse() {
  if (window.pywebview?.api?.browse) {
    importPaths(await window.pywebview.api.browse());
    return;
  }
  const p = prompt("Paste the full path of a recording or a folder of recordings:");
  if (p) importPaths([p]);
}

let dragDepth = 0;
document.addEventListener("dragenter", ev => {
  if (![...(ev.dataTransfer?.types || [])].includes("Files")) return;
  dragDepth++;
  $("#dropzone").hidden = false;
});
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropzone").hidden = true; } });
document.addEventListener("dragover", ev => ev.preventDefault());
document.addEventListener("drop", ev => {
  ev.preventDefault();
  dragDepth = 0;
  $("#dropzone").hidden = true;
  // In the desktop app the drop is handled natively (it knows the real file paths).
  if (!window.pywebview) alert("Drag and drop works in the Clip Studio desktop app. Here, use Import instead.");
});

// ------------------------------------------------------------------ wiring

$("#import").onclick = browse;
$("#import2").onclick = browse;
$("#recording").onchange = ev => {
  const v = ev.target.value;
  if (v.startsWith("replay:")) showReplay(v.slice("replay:".length));
  else selectRecording(v);
};
$("#rv-open").onclick = () => {
  const r = state.replays.find(x => x.path === state.replaySel);
  if (r?.recording) selectRecording(r.recording);
};
$("#rv-choose").onclick = async () => {
  let paths;
  if (window.pywebview?.api?.browse) paths = await window.pywebview.api.browse();
  else { const p = prompt("Paste the full path of the recording of this match:"); paths = p ? [p] : []; }
  const video = (paths || []).find(p => !p.toLowerCase().endsWith(".replay"));
  if (!video) return;
  try {
    const res = await api("/api/replay/pair", { replay: state.replaySel, video });
    await refreshState();
    await selectRecording(res.recording);
  } catch (e) { report(e); }
};
$("#analyze").onclick = async () => {
  const v = state.videos.find(x => x.name === state.current);
  if (!v) return;
  if (v.scanned && !confirm("Analyze this recording again? Your trims and edits for it will be replaced.")) return;
  try { await api("/api/analyze", { video: v.name, rescan: v.scanned, speed: $("#speed").value }); refreshState(); }
  catch (e) { report(e); }
};
$("#status").onclick = () => { $("#tasks").hidden = !$("#tasks").hidden; renderTasks(); };
$("#cancel").onclick = () => {
  const running = state.jobs.find(j => j.status === "running");
  if (running) cancelJob(running.id);
};
$("#remove").onclick = async () => {
  if (state.replaySel) {
    const r = state.replays.find(x => x.path === state.replaySel);
    if (!confirm(`Remove the replay ${r?.name || r?.file} from the list? The file stays on your PC.`)) return;
    try { await api("/api/replay/remove", { replay: state.replaySel }); } catch (e) { return report(e); }
    state.replaySel = null;
    await refreshState();
    return;
  }
  const name = state.current;
  if (!name || !confirm(`Remove ${name} from the list?\n\nIts tasks are cancelled. The video file and any clips you exported stay on your PC.`)) return;
  try {
    await api("/api/remove", { video: name });
    state.current = null;
    state.clips = [];
    player.removeAttribute("src");
    player.load();
    await refreshState();
    if (state.current) await loadClips(true); else renderAll();
  } catch (e) { report(e); }
};
$("#update").onclick = async () => {
  const btn = $("#update");
  if (!confirm(`Install ${btn.textContent.replace("\u2B06 Update to ", "version ")}?\n\nClip Studio restarts afterwards. Your clips, music and settings are kept.`)) return;
  try { await api("/api/update", {}); btn.hidden = true; refreshState(); } catch (e) { report(e); }
};
$("#check-update").onclick = async () => {
  try {
    const u = await api("/api/update/check", {});
    renderUpdate(u);
    alert(u.available ? `Version ${u.latest} is available.` : u.latest ? `You're up to date (${u.current}).`
      : "Couldn't check for updates right now.");
  } catch (e) { report(e); }
};
document.addEventListener("click", ev => {
  if (!ev.target.closest("#tasks, #status, #cancel")) $("#tasks").hidden = true;
});

$("#speed").value = load("speed", "standard");
$("#speed").onchange = ev => store("speed", ev.target.value);
$("#sort").onchange = ev => { state.sort = ev.target.value; renderList(); };
$("#show-removed").onchange = ev => { state.showRemoved = ev.target.checked; renderAll(); };

$("#prev").onclick = () => step(-1);
$("#next").onclick = () => step(1);
$("#playclip").onclick = playClip;
$("#playpause").onclick = () => (player.paused ? player.play() : player.pause());
$("#add-here").onclick = async () => {
  if (!state.current) return;
  try {
    const row = await api("/api/add", { video: state.current, at: player.currentTime });
    await loadClips(false);
    selectClip(Number(row.clip));
  } catch (e) { report(e); }
};

$("#ed-keep").onclick = () => {
  const c = selClip();
  save({ skip: !c.skip });
  renderAll();
};
$("#ed-category").onchange = ev => { save({ category: ev.target.value }); renderAll(); };
for (const b of $$("[data-nudge]")) {
  b.onclick = () => {
    const [which, d] = b.dataset.nudge.split(":");
    setTrim(which, selClip()[which] + parseFloat(d));
  };
}
$("#set-start").onclick = () => setTrim("start", player.currentTime);
$("#set-end").onclick = () => setTrim("end", player.currentTime);
$("#ed-caption").oninput = ev => save({ caption: ev.target.value });
$("#ed-shuffle").onclick = async () => {
  const c = selClip();
  try {
    const s = await api("/api/caption", { label: c.label, tone: c.tone });
    save({ caption: s.caption, tone: s.tone });
    $("#ed-caption").value = s.caption;
  } catch (e) { report(e); }
};
$("#ed-music").onchange = ev => save({ music: ev.target.value });
$("#ed-game").oninput = ev => { save({ game_volume: Number(ev.target.value) }); renderEditor(); };
$("#ed-musicvol").oninput = ev => { save({ music_volume: Number(ev.target.value) }); renderEditor(); };
for (const b of $$("#ed-framing button")) b.onclick = () => { save({ framing: b.dataset.framing }); renderEditor(); };
$("#ed-cropx").oninput = ev => { save({ crop_x: Number(ev.target.value) }); renderEditor(); };

$("#ed-export").onclick = async () => {
  const c = selClip();
  try { await api("/api/export", { video: state.current, clips: [c.clip] }); refreshState(); } catch (e) { report(e); }
};
$("#ed-watch").onclick = () => openModal(selClip().rendered);
$("#export-kept").onclick = async () => {
  const kept = visibleClips().filter(c => !c.skip).map(c => c.clip);
  try { await api("/api/export", { video: state.current, clips: kept }); refreshState(); } catch (e) { report(e); }
};
$("#compile").onclick = async () => {
  const kept = state.clips.filter(c => !c.skip && !state.hidden.has(c.category))
    .sort((a, b) => a.anchor - b.anchor).map(c => c.clip);
  if (!confirm(`Join ${kept.length} kept clips (in recording order) into one highlight video?`)) return;
  try { await api("/api/compile", { video: state.current, clips: kept }); refreshState(); } catch (e) { report(e); }
};
$("#open-folder").onclick = () => api("/api/open", { video: state.current }).catch(report);

function openModal(url) {
  $("#modal-video").src = url;
  $("#modal").hidden = false;
  player.pause();
  $("#modal-video").play().catch(() => {});
}
function closeModal() { $("#modal-video").pause(); $("#modal").hidden = true; }
$("#modal-close").onclick = closeModal;
$("#modal").addEventListener("click", ev => { if (ev.target.id === "modal") closeModal(); });

document.addEventListener("keydown", ev => {
  if (ev.key === "Escape") return closeModal();
  if (typing() || !selClip()) return;
  const k = ev.key.toLowerCase();
  if (k === " ") { ev.preventDefault(); player.paused ? player.play() : player.pause(); }
  else if (k === "arrowdown") { ev.preventDefault(); step(1); }
  else if (k === "arrowup") { ev.preventDefault(); step(-1); }
  else if (k === "p") playClip();
  else if (k === "i") setTrim("start", player.currentTime);
  else if (k === "o") setTrim("end", player.currentTime);
  else if (k === "delete" || k === "backspace") $("#ed-keep").click();
});

refreshState();
setInterval(refreshState, 1500);
