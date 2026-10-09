// 観戦ビューア（と GUI の対局）の起動・画面操作・更新を調停する。
import { $, el } from "./dom.js";
import { createSource, latestLoader } from "./source.js";
import { Timeline } from "./timeline.js";
import { createRenderer } from "./render.js";
import { createPlay } from "./play.js";

// play: 席の鍵を持って対局しているときの {seat}（無ければ観戦）。marks: 組み立て中の Act で選んだもの
const ui = { game: null, seat: "judge", live: true, pos: 0, cursor: 0, view: null, log: [], names: {},
  images: true, motion: true, open: new Set(), attacking: new Set(), blocking: new Set(), sides: {}, timer: null,
  play: null, marks: new Map() };
let config = { play: false };
const source = createSource(document.documentElement.dataset.static === "1");
const loadLatest = latestLoader(source);
let timeline = null;
let unsubscribe = () => {};
const viewAt = (pos) => timeline.at(pos);
const hooks = {};  // GUI の対局のときだけ play のクリックの受け口を入れる
const { render, renderLog, placeSpeech, showCard, toggleOpen } = createRenderer(ui, {
  source, viewAt, clampFloats, hooks,
  onLogSeek(pos) {
    stopPlay();
    ui.keepLogScroll = true;
    ui.deltaBase = null;
    ui.live = false;
    ui.pos = Math.min(pos, ui.cursor);
    show();
  },
});

function remember(key, value) {
  try { localStorage.setItem("mtgtable." + key, value); } catch (_) { /* 保存できなくても動く */ }
}
function recall(key) {
  try { return localStorage.getItem("mtgtable." + key); } catch (_) { return null; }
}

const play = createPlay(ui, { source, reload: () => load(), showCard, toggleOpen, render });

// ---------------------------------------------------------------- 席の鍵（GUI の対局）

// invite の URL（?game=G&seat=p1#key=...）から鍵を受け取って覚え、アドレス・バーから消す
const params = new URLSearchParams(location.search);
(() => {
  const key = new URLSearchParams(location.hash.slice(1)).get("key");
  if (params.get("game") && params.get("seat") && key) {
    remember("key." + params.get("game"), JSON.stringify({ seat: params.get("seat"), token: key }));
    history.replaceState(null, "", location.pathname + location.search);
  }
})();

function keyOf(game) {
  try { return JSON.parse(recall("key." + game) || "null"); } catch (_) { return null; }
}

// 選んだ対局の鍵を持っていれば、その席で対局する（席は固定）。無ければ観戦
function applySeatMode() {
  const k = config.play && ui.game ? keyOf(ui.game) : null;
  ui.play = k ? { seat: k.seat } : null;
  source.setKey(k ? { game: ui.game, seat: k.seat, token: k.token } : null);
  for (const name of ["card", "drop", "mana", "stack", "player", "pile", "after", "preview", "retarget"]) delete hooks[name];
  if (k) {
    Object.assign(hooks, play.hooks);
    ui.seat = k.seat;
  } else if ($("seat").disabled) {
    ui.seat = recall("seat") || "judge";  // 対局の席から観戦に戻ったら、観戦で選んでいた席に
  }
  play.reset();
  $("seat").value = ui.seat;
  $("seat").disabled = !!k;
  $("playing").hidden = !k;
  $("playing").textContent = k ? `${k.seat} で対局中` : "";
  $("fl-play").hidden = !k;
  document.body.classList.toggle("playing", !!k);
  document.title = k ? `mtgtable 対局 ${ui.game}` : "mtgtable 観戦";
}

// ---------------------------------------------------------------- 読み込み

async function loadGames() {
  const games = await source.games();
  const sel = $("game");
  sel.replaceChildren(...games.map((g) => {
    const o = el("option", null, `${g.id}（T${g.turn}・v${g.version}）`);
    o.value = g.id;
    return o;
  }));
  const saved = params.get("game") || recall("game");
  ui.game = games.some((g) => g.id === saved) ? saved : (games[0] && games[0].id);
  if (ui.game) sel.value = ui.game;
}

// 時系列と Log は同じ選択の結果をまとめて反映する。
async function load() {
  if (!ui.game) return;
  const result = await loadLatest(ui.game, ui.seat);
  if (!result) return;
  // Live で見ているときの更新: 1件ならカードを動かし、それより多ければ前の最新から再生して遷移を見せる（最後で Live に戻る）。
  // 再生中に届いた更新は、そのまま再生の続きになる
  const from = ui.cursor;
  const added = result.timeline.cursor - from;
  const follow = timeline && ui.live && added > 1;
  if (timeline && ui.live && added === 1) ui.animate = { duration: STEP_MS };
  timeline = new Timeline(result.timeline);
  ui.log = result.log;
  ui.cursor = timeline.cursor;
  if (follow) return startPlay(from);
  show();
}

function show() {
  if (!timeline) return;
  if (ui.live) ui.pos = ui.cursor;
  ui.pos = Math.max(0, Math.min(ui.pos, ui.cursor));
  ui.view = viewAt(ui.pos);
  render();
}

function listen() {
  unsubscribe();
  if (!ui.game) return;
  unsubscribe = source.subscribe(ui.game, (msg) => {
    const last = timeline && viewAt(ui.cursor);
    if (!last || msg.cursor !== ui.cursor || msg.version !== last.version) load().catch(showError);
  }, (connected) => $("conn").classList.toggle("ok", connected));
}

function changeSelection() {
  stopPlay();
  applySeatMode();
  ui.open.clear();
  ui.handOrder = {};
  ui.deltaBase = null;
  timeline = null;
  ui.view = null;
  ui.shown = null;
  ui.log = [];
  ui.cursor = 0;
  for (const id of ["top", "bottom", "log", "detail"]) $(id).replaceChildren();
  for (const id of ["fl-detail", "fl-stack", "combatbox"]) $(id).hidden = true;
  load().catch(showError);
  listen();
}

function showError(e) {
  // 鍵で守っている対局を、鍵の無いページ・席で開いたとき（localhost と 127.0.0.1 は別のサイトなので、鍵も別に覚える）
  $("detail").textContent = /needs its key/.test(e.message)
    ? `この対局は席の鍵が必要です。invite が出した URL（${location.hostname === "localhost" ? "127.0.0.1 の方" : "#key= 付き"}）を`
      + `このブラウザで開いてください。作り直すには: python -m mtgtable invite playtest/${ui.game} --seat p1`
    : "エラー: " + e.message;
  $("fl-detail").hidden = false;
}

// ---------------------------------------------------------------- 操作

const STEP_MS = 280;  // 1件ずつ進める・戻すときのカードの動きの長さ

// 1件ずつの移動。カードを動かす
function step(delta) {
  ui.animate = { duration: STEP_MS };
  seek(ui.pos + delta);
}

function seek(pos) {
  ui.deltaBase = null;
  ui.pos = Math.max(0, Math.min(pos, ui.cursor));
  ui.live = ui.pos >= ui.cursor;
  return show();
}

// ---------------------------------------------------------------- 自動再生

// 次に進める位置。Batch ごとなら、今の位置より後で最初に終わる Batch の最後の Act まで（Log がある judge の席だけ）
function nextPos() {
  if ($("unit").value === "batch" && ui.log.length) {
    const after = ui.log.filter((e) => e.seq > ui.pos && !e.undone);
    if (after.length) {
      const batch = after[0].batch;
      return after.filter((e) => e.batch === batch).pop().seq;
    }
  }
  return ui.pos + 1;
}

function stopPlay() {
  clearTimeout(ui.timer);
  ui.timer = null;
  ui.playing = false;
  $("play").textContent = "▶ 再生";
  $("play").classList.remove("on");
}

function startPlay(from = null) {
  if (!timeline) return;
  if (from !== null) ui.pos = from;  // 更新の追いかけ: 前に見ていた最新から
  else if (ui.pos >= ui.cursor) ui.pos = 0;  // 最後まで見ていたら最初から
  ui.live = false;
  ui.playing = true;
  $("play").textContent = "⏸ 停止";
  $("play").classList.add("on");
  const step = () => {
    if (ui.pos >= ui.cursor) {  // 最後まで来たら止めて Live に戻る
      stopPlay();
      ui.live = true;
      return show();
    }
    const from = ui.pos;
    ui.pos = Math.min(nextPos(), ui.cursor);
    ui.deltaBase = { pos: ui.pos, from };  // 自動再生では、ライフの差を1回前に表示した位置から数える
    ui.live = false;
    ui.animate = { duration: Math.min(450, Number($("speed").value) * 0.7) };
    show();
    if (ui.timer !== null) ui.timer = setTimeout(step, Number($("speed").value));
  };
  ui.timer = setTimeout(step, 0);
}

const manual = (f) => (...args) => { stopPlay(); return f(...args); };

// ---------------------------------------------------------------- 小窓と Log

// 小窓: 見出しをドラッグで盤面の中を移動（位置は覚える）、– で畳む、× で閉じる
const floatClamps = [];  // 盤面の大きさが変わったら（Log の開閉・窓の大きさ）、動かした小窓を盤面の中に戻す
function clampFloats() { floatClamps.forEach((f) => f()); }
addEventListener("resize", () => { placeSpeech(); clampFloats(); });

function setupFloat(panel) {
  const key = "float." + panel.id;
  const board = panel.parentElement;
  const place = (x, y) => {
    const b = board.getBoundingClientRect();
    const nx = Math.max(0, Math.min(x, b.width - panel.offsetWidth));
    const ny = Math.max(0, Math.min(y, b.height - 28));
    Object.assign(panel.style, { left: `${nx}px`, top: `${ny}px`, right: "auto", bottom: "auto" });
    return [nx, ny];
  };
  let pending = null;  // 覚えた位置。盤面が描かれて大きさが決まってから当てる
  try {
    pending = JSON.parse(recall(key) || "null");
    if (pending) panel.classList.toggle("min", !!pending.min);
  } catch (_) { /* 覚えた位置が無くても動く */ }
  floatClamps.push(() => {
    const b = board.getBoundingClientRect();
    if (pending && !panel.hidden && panel.offsetWidth && b.width > panel.offsetWidth && b.height > 60) {
      place(pending.x, pending.y);
      pending = null;
    }
    else if (panel.style.left) place(panel.offsetLeft, panel.offsetTop);
  });
  const save = () => remember(key, JSON.stringify({ x: panel.offsetLeft, y: panel.offsetTop, min: panel.classList.contains("min") }));
  panel.querySelector(".fmin").onclick = () => { panel.classList.toggle("min"); save(); };
  const close = panel.querySelector(".fclose");
  if (close) close.onclick = () => { panel.hidden = true; };
  panel.querySelector(".fhead").addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const pb = panel.getBoundingClientRect(), bb = board.getBoundingClientRect();
    const start = { x: e.clientX, y: e.clientY, left: pb.left - bb.left, top: pb.top - bb.top };
    const move = (ev) => place(start.left + ev.clientX - start.x, start.top + ev.clientY - start.y);
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); save(); };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    e.preventDefault();
  });
}
document.querySelectorAll(".float").forEach(setupFloat);

// Log: 開発者ツールのように右に寄せて開閉し、境目をドラッグで幅を変える（開閉と幅は覚える）
function setLog(open) {
  document.body.classList.toggle("log-open", open);
  $("logToggle").classList.toggle("on", open);
  remember("logOpen", open ? "1" : "0");
  if (open) renderLog();
  requestAnimationFrame(clampFloats);
}
$("logToggle").onclick = () => setLog(!document.body.classList.contains("log-open"));
$("logClose").onclick = () => setLog(false);
$("logResizer").addEventListener("pointerdown", (e) => {
  const move = (ev) => {
    const w = Math.max(240, Math.min(window.innerWidth - ev.clientX, window.innerWidth * 0.6));
    document.documentElement.style.setProperty("--logw", `${w}px`);
  };
  const up = () => {
    removeEventListener("pointermove", move); removeEventListener("pointerup", up);
    placeSpeech();
    clampFloats();
    remember("logWidth", getComputedStyle(document.documentElement).getPropertyValue("--logw").trim());
  };
  addEventListener("pointermove", move);
  addEventListener("pointerup", up);
  e.preventDefault();
});
if (recall("logWidth")) document.documentElement.style.setProperty("--logw", recall("logWidth"));
setLog(recall("logOpen") === "1");

$("game").onchange = (e) => { ui.game = e.target.value; remember("game", ui.game); ui.live = true; changeSelection(); };
// カードの動き。OS で「動きを減らす」にしている人には動かさない
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const setMotion = () => { ui.motion = $("motion").checked && !reduceMotion.matches; };
$("motion").onchange = () => { remember("motion", $("motion").checked ? "1" : "0"); setMotion(); };
reduceMotion.addEventListener("change", setMotion);
$("images").onchange = (e) => { ui.images = e.target.checked; remember("images", ui.images ? "1" : "0"); render(); };
$("seat").onchange = (e) => { ui.seat = e.target.value; remember("seat", ui.seat); changeSelection(); };
$("pos").oninput = manual((e) => seek(Number(e.target.value)));
$("first").onclick = manual(() => seek(0));
$("prev").onclick = manual(() => step(-1));
$("next").onclick = manual(() => step(1));
$("live").onclick = manual(() => { ui.live = true; show(); });
$("play").onclick = () => (ui.timer !== null && ui.timer !== undefined ? stopPlay() : startPlay());
$("unit").onchange = (e) => remember("unit", e.target.value);
$("speed").onchange = (e) => remember("speed", e.target.value);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || $("ask").open) return;
  if (e.key === "ArrowLeft") manual(() => step(-1))();
  if (e.key === "ArrowRight") manual(() => step(1))();
  if (e.key === " ") { e.preventDefault(); $("play").click(); }
  if (e.key === "l" || e.key === "L") $("logToggle").click();
});

(async () => {
  try {
    await source.init();
    config = await source.config().catch(() => ({ play: false }));
    if (source.isStatic) {
      $("seat").replaceChildren(...[...$("seat").options].filter((o) => o.value === "judge"));
      $("conn").hidden = true;
      remember("seat", "judge");
    }
    ui.seat = recall("seat") || "judge";
    $("seat").value = ui.seat;
    ui.images = recall("images") !== "0";
    $("images").checked = ui.images;
    $("motion").checked = recall("motion") !== "0";
    setMotion();
    if (recall("unit")) $("unit").value = recall("unit");
    if (recall("speed")) $("speed").value = recall("speed");
    await loadGames();
    applySeatMode();
    await load();
    listen();
  } catch (e) { showError(e); }
})();
