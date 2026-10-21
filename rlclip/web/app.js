"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const state = { folder: "", videos: [], jobs: [], music: [], current: null, clips: [], filter: "all" };
const jobStatus = new Map();   // job id -> last seen status, to notice when jobs finish

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function report(err) {
  console.error(err);
  alert(err.message || String(err));
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

const fmtRel = t => (t >= 0 ? "+" : "−") + Math.abs(t).toFixed(1) + "s";

// ------------------------------------------------------------------ sidebar

async function refreshState() {
  let data;
  try { data = await api("/api/state"); } catch (e) { return; }
  state.videos = data.videos;
  state.jobs = data.jobs;
  state.music = data.music;
  if (!state.folder && data.folder) $("#folder").value = data.folder;
  state.folder = data.folder;
  renderVideos();
  renderJobs();

  for (const job of data.jobs) {
    const before = jobStatus.get(job.id);
    jobStatus.set(job.id, job.status);
    if (before === undefined || before === job.status) continue;
    if ((job.status === "done" || job.status === "error") && job.video === state.current) {
      if (job.kind === "scan") loadClips();
      else softRefresh();
    }
  }
}

function busyJob(name) {
  return state.jobs.find(j => j.video === name && (j.status === "queued" || j.status === "running"));
}

function renderVideos() {
  const list = $("#videos");
  list.innerHTML = "";
  if (!state.videos.length) {
    list.innerHTML = `<li class="muted">${state.folder ? "No videos in this folder" : "Open a folder above"}</li>`;
    return;
  }
  for (const v of state.videos) {
    const li = document.createElement("li");
    li.className = v.name === state.current ? "active" : "";
    const job = busyJob(v.name);
    const status = job ? job.message : v.scanned ? `${v.clips} clip${v.clips === 1 ? "" : "s"}` : "Not scanned";
    li.innerHTML = `<div><div class="name"></div><div class="status"></div></div><button></button>`;
    $(".name", li).textContent = v.name;
    $(".status", li).textContent = status;
    const btn = $("button", li);
    btn.textContent = job ? "Working..." : v.scanned ? "Rescan" : "Scan";
    btn.disabled = Boolean(job);
    btn.onclick = async ev => {
      ev.stopPropagation();
      if (v.scanned && !confirm("Scan this recording again? Your trims and caption edits for it will be replaced.")) return;
      try {
        await api("/api/scan", { video: v.name, rescan: v.scanned });
        selectVideo(v.name);
        refreshState();
      } catch (e) { report(e); }
    };
    li.onclick = () => selectVideo(v.name);
    list.appendChild(li);
  }
}

function renderJobs() {
  const box = $("#jobs");
  const active = state.jobs.filter(j => j.status !== "done").slice(-6);
  box.innerHTML = "";
  for (const job of active) {
    const el = document.createElement("div");
    el.className = "job" + (job.status === "error" ? " error" : "");
    el.innerHTML = `<div class="msg"></div><div class="bar"><div class="fill"></div></div>`;
    $(".msg", el).textContent = `${job.video}: ${job.status === "error" ? "Failed - " : ""}${job.message}`;
    $(".fill", el).style.width = `${job.progress}%`;
    box.appendChild(el);
  }
}

// ------------------------------------------------------------------ clips

async function selectVideo(name) {
  state.current = name;
  renderVideos();
  $("#empty").hidden = true;
  $("#workspace").hidden = false;
  $("#title").textContent = name;
  await loadClips();
}

async function loadClips() {
  if (!state.current) return;
  try {
    state.clips = await api(`/api/clips?video=${encodeURIComponent(state.current)}`);
  } catch (e) { return report(e); }
  renderClips();
}

async function softRefresh() {
  // Update previews / finished renders without rebuilding cards (keeps videos playing).
  let fresh;
  try { fresh = await api(`/api/clips?video=${encodeURIComponent(state.current)}`); } catch (e) { return; }
  if (fresh.length !== state.clips.length) { state.clips = fresh; return renderClips(); }
  fresh.forEach((c, i) => {
    const old = state.clips[i];
    old.rendered = c.rendered;
    if (!old.preview && c.preview) { old.preview = c.preview; old.preview_start = c.preview_start; }
    old.card?.refresh();
  });
}

function updateSummary() {
  const kept = state.clips.filter(c => !c.skip).length;
  $("#summary").textContent = state.clips.length
    ? `${state.clips.length} clip${state.clips.length === 1 ? "" : "s"} found · ${kept} kept`
    : busyJob(state.current) ? "Scanning..." : "No clips yet - click Scan on the left";
  $("#render-all").disabled = kept === 0;
  $("#render-all").textContent = `Render kept clips (${kept})`;
}

function applyFilter() {
  for (const c of state.clips) {
    const show = state.filter === "all" || (state.filter === "kept") === !c.skip;
    c.card.el.hidden = !show;
  }
}

function renderClips() {
  const box = $("#clips");
  box.innerHTML = "";
  for (const clip of state.clips) {
    clip.card = makeCard(clip);
    box.appendChild(clip.card.el);
  }
  updateSummary();
  applyFilter();
}

function makeCard(clip) {
  const el = $("#clip-template").content.firstElementChild.cloneNode(true);
  const video = $("video", el);
  const track = $(".track", el);
  const caption = $(".caption", el);
  const musicSel = $(".music", el);
  let duration = 0;
  let playingClip = false;

  $(".num", el).textContent = `#${String(clip.clip).padStart(2, "0")}`;
  $(".label", el).textContent = clip.label;
  $(".score", el).textContent = `score ${Number(clip.score).toFixed(1)}`;
  caption.value = clip.caption;

  musicSel.innerHTML = `<option value="">Auto (matches the play)</option>`;
  for (const m of state.music) {
    const opt = document.createElement("option");
    opt.value = m.split("/").pop();
    opt.textContent = m;
    musicSel.appendChild(opt);
  }
  musicSel.value = clip.music || "";

  // Times inside the preview video.
  const local = t => t - clip.preview_start;
  const pct = t => duration ? `${Math.min(100, Math.max(0, (t / duration) * 100))}%` : "0%";

  function draw() {
    const s = local(clip.start), e = local(clip.end), a = local(clip.anchor);
    $(".range", el).style.left = pct(s);
    $(".range", el).style.width = duration ? `${((e - s) / duration) * 100}%` : "0%";
    $(".handle.start", el).style.left = pct(s);
    $(".handle.end", el).style.left = pct(e);
    $(".anchor", el).style.left = pct(a);
    $(".t-start", el).textContent = `Start ${fmtRel(clip.start - clip.anchor)}`;
    $(".t-end", el).textContent = `End ${fmtRel(clip.end - clip.anchor)}`;
    $(".length", el).textContent = `${(clip.end - clip.start).toFixed(1)}s`;
  }

  function refresh() {
    if (clip.preview && !video.src) {
      video.src = clip.preview;
      $(".loading", el).hidden = true;
    } else if (!clip.preview) {
      $(".loading", el).hidden = false;
    }
    el.classList.toggle("discarded", clip.skip);
    const keep = $(".keep", el);
    keep.textContent = clip.skip ? "Discarded — keep it" : "Kept — discard";
    keep.className = "keep " + (clip.skip ? "is-discarded" : "is-kept");
    $(".view", el).hidden = !clip.rendered;
    $("[data-act=render]", el).textContent = clip.rendered ? "Re-render" : "Render";
    draw();
  }

  const save = async fields => {
    try { await api("/api/clip", { video: state.current, clip: clip.clip, fields }); }
    catch (e) { report(e); }
  };
  const saveTrim = debounce(() => save({ start: clip.start, end: clip.end }), 350);
  const saveCaption = debounce(() => save({ caption: clip.caption }), 500);

  function setTrim(which, t) {
    // Times are absolute (seconds into the recording); keep at least 1s and inside the preview.
    const min = clip.preview_start, max = clip.preview_start + (duration || 1e9);
    if (which === "start") clip.start = Math.min(Math.max(t, min), clip.end - 1);
    else clip.end = Math.max(Math.min(t, max), clip.start + 1);
    clip.start = Math.round(clip.start * 100) / 100;
    clip.end = Math.round(clip.end * 100) / 100;
    draw();
    saveTrim();
  }

  video.addEventListener("loadedmetadata", () => { duration = video.duration; draw(); });
  video.addEventListener("timeupdate", () => {
    $(".playhead", el).style.left = pct(video.currentTime);
    if (playingClip && video.currentTime >= local(clip.end)) {
      video.pause();
      playingClip = false;
    }
  });
  video.addEventListener("pause", () => { playingClip = false; });

  // Dragging handles, or clicking the bar to jump there.
  const timeAt = ev => {
    const r = track.getBoundingClientRect();
    return clip.preview_start + Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)) * duration;
  };
  track.addEventListener("pointerdown", ev => {
    if (!duration) return;
    const handle = ev.target.closest(".handle");
    if (!handle) { video.currentTime = local(timeAt(ev)); return; }
    const which = handle.classList.contains("start") ? "start" : "end";
    track.setPointerCapture(ev.pointerId);
    const move = e => { setTrim(which, timeAt(e)); video.currentTime = local(which === "start" ? clip.start : clip.end); };
    const up = () => { track.removeEventListener("pointermove", move); track.removeEventListener("pointerup", up); };
    track.addEventListener("pointermove", move);
    track.addEventListener("pointerup", up);
  });

  el.addEventListener("click", async ev => {
    const act = ev.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    const now = clip.preview_start + video.currentTime;
    switch (act) {
      case "start-": setTrim("start", clip.start - 0.5); break;
      case "start+": setTrim("start", clip.start + 0.5); break;
      case "end-": setTrim("end", clip.end - 0.5); break;
      case "end+": setTrim("end", clip.end + 0.5); break;
      case "set-start": setTrim("start", now); break;
      case "set-end": setTrim("end", now); break;
      case "play":
        video.currentTime = local(clip.start);
        playingClip = true;
        video.play();
        break;
      case "shuffle":
        try {
          const s = await api("/api/caption", { label: clip.label, tone: clip.tone });
          clip.caption = caption.value = s.caption;
          save({ caption: clip.caption });
        } catch (e) { report(e); }
        break;
      case "toggle":
        clip.skip = !clip.skip;
        await save({ skip: clip.skip });
        refresh();
        updateSummary();
        applyFilter();
        break;
      case "render":
        try {
          ev.target.textContent = "Queued...";
          ev.target.disabled = true;
          await api("/api/render", { video: state.current, clips: [clip.clip] });
          refreshState();
        } catch (e) { report(e); }
        setTimeout(() => { ev.target.disabled = false; }, 1500);
        break;
      case "view":
        openModal(clip);
        break;
    }
  });

  caption.addEventListener("input", () => { clip.caption = caption.value; saveCaption(); });
  musicSel.addEventListener("change", () => { clip.music = musicSel.value; save({ music: clip.music }); });

  refresh();
  return { el, refresh };
}

function openModal(clip) {
  $("#modal-video").src = clip.rendered;
  $("#modal-caption").textContent = clip.caption;
  $("#modal").hidden = false;
  $("#modal-video").play();
}

function closeModal() {
  $("#modal-video").pause();
  $("#modal").hidden = true;
}

// ------------------------------------------------------------------ wiring

$("#folder-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  try {
    await api("/api/folder", { folder: $("#folder").value });
    state.current = null;
    $("#workspace").hidden = true;
    $("#empty").hidden = false;
    refreshState();
  } catch (e) { report(e); }
});

$("#filter").addEventListener("click", ev => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  state.filter = btn.dataset.filter;
  for (const b of $("#filter").children) b.classList.toggle("active", b === btn);
  applyFilter();
});

$("#render-all").addEventListener("click", async () => {
  const kept = state.clips.filter(c => !c.skip).map(c => c.clip);
  if (!kept.length) return;
  try { await api("/api/render", { video: state.current, clips: kept }); refreshState(); }
  catch (e) { report(e); }
});

$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", ev => { if (ev.target.id === "modal") closeModal(); });
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeModal(); });

refreshState();
setInterval(refreshState, 1500);
