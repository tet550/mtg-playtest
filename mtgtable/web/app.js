// 観戦ビューア（と GUI の対局）の起動・画面操作・更新を調停する。
import { $, el } from "./dom.js";
import { createSource, latestLoader } from "./source.js";
import { Timeline } from "./timeline.js";
import { createRenderer } from "./render.js";
import { createPlay, toast } from "./play.js";
import { createSite, routeOf } from "./site.js";
import { applyDom, onLang, setLang, t } from "./i18n.js";

applyDom();  // 今の言語の文言を HTML の印に入れる（描く前に）

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
const { render, renderLog, placeSpeech, showCard, toggleOpen, manaNodes } = createRenderer(ui, {
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

const play = createPlay(ui, { source, reload: () => load(), showCard, toggleOpen, render, manaNodes });

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

let mySeats = {};  // 公開のサーバーで、所有者の鍵（Cookie）で持っている席 {対局: 席}
let gameList = [];
let viewing = null;  // 再生・観戦（#/view/<対局>[/<共有の鍵>]）: {game, share, finished, views}
const VIEW_LABEL = (v) => (v === "judge" ? t("seat.all") : v);
let site = null;  // 公開のサーバーの画面の切り替え（トップ・デッキ・対局）
function keyOf(game) {
  let k = null;
  try { k = JSON.parse(recall("key." + game) || "null"); } catch (_) { k = null; }
  if (mySeats[game] && (!k || k.seat !== mySeats[game])) k = { seat: mySeats[game], token: null };
  return k;
}

// 選んだ対局の鍵を持っていれば、その席で対局する（席は固定）。無ければ観戦
function applySeatMode() {
  // 再生・観戦（#/view）では、自分の席でも対局が終わっていれば操作しない（どの視点でも見られる）
  const k = config.play && ui.game && !(viewing && viewing.finished) ? keyOf(ui.game) : null;
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
  $("fl-play").hidden = !k;
  document.body.classList.toggle("playing", !!k);
  seatLabels();
}

// 席の表示（対局中の印・ページの題）。言語を変えたときも呼ぶ
function seatLabels() {
  $("playing").textContent = ui.play ? t("header.playing", { seat: ui.play.seat }) : "";
  document.title = ui.play ? t("title.play", { game: ui.game }) : t("title.watch");
}

// 対局の選択肢の名前（一覧の対局と、再生・観戦で足した対局）
function gameLabel(id) {
  const g = gameList.find((x) => x.id === id);
  if (g) return t("game.option", { id: g.id, turn: g.turn, version: g.version });
  return t(viewing && viewing.finished ? "game.option.replay" : "game.option.watch", { id });
}

// ---------------------------------------------------------------- 読み込み

async function loadGames() {
  const games = await source.games();
  gameList = games;
  mySeats = Object.fromEntries(games.filter((g) => g.my_seat).map((g) => [g.id, g.my_seat]));
  const sel = $("game");
  sel.replaceChildren(...games.map((g) => {
    const o = el("option", null, gameLabel(g.id));
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
  ui.idle = result.timeline.idle ? { ...result.timeline.idle, at: Date.now() } : null;  // 時間切れの知らせ（公開のサーバー）
  ui.ai = result.timeline.ai || null;  // サーバーの中で回す AI の状態（中断していれば理由）
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

// 公開のサーバー: 復元 URL（#recover=...）で開いたら、この端末を同じ所有者に戻す。ヘッダーに復元 URL を作るボタン
async function siteSetup() {
  const token = new URLSearchParams(location.hash.slice(1)).get("recover");
  if (token) {
    history.replaceState(null, "", location.pathname + location.search);
    try {
      await source.recover(token);
    } catch (e) {
      showError(new Error(t("error.recover", { message: e.message })));
    }
  }
  // 招待の URL（?game=G&seat=p1#key=...）で来たら、その席をこの所有者のものにする（取った後は鍵が無くても入れる）
  const game = params.get("game"), invited = game && keyOf(game);
  if (invited && invited.token) {
    try {
      await source.claim(game, invited.seat, invited.token);
    } catch (e) {
      showError(new Error(e.status === 403 ? t("error.seatTaken") : e.message));
    }
  }
  $("seat").replaceChildren(...[...$("seat").options].filter((o) => o.value !== "judge"));
  site = createSite(source, { toast, games: () => gameList, openGame, config: () => config });
  $("recovery").hidden = false;
  $("legallinks").hidden = false;
  $("recovery").onclick = async () => {
    try {
      const { token } = await source.recovery();
      window.prompt(t("recovery.prompt"), `${location.origin}/#recover=${token}`);
    } catch (e) { showError(e); }
  };
}

// 再生・観戦: 見られる視点（サーバーの access）を席の選択に出し、その対局を開く。共有の鍵は読み取りに付ける
async function openView(game, share) {
  const a = await source.access(game, share);
  viewing = { game, share, finished: a.finished, views: a.views };
  source.setViewing({ game, share });
  const sel = $("game");
  if (![...sel.options].some((o) => o.value === game)) {
    const o = el("option", null, gameLabel(game));
    o.value = game;
    sel.append(o);
  }
  $("seat").replaceChildren(...a.views.map((v) => { const o = el("option", null, VIEW_LABEL(v)); o.value = v; return o; }));
  ui.game = game;
  sel.value = game;
  ui.seat = a.views.includes(ui.seat) ? ui.seat : a.views[0];
  ui.live = true;
  changeSelection();
  $("seat").value = ui.seat;
}

async function closeView() {
  viewing = null;
  source.setViewing(null);
  $("seat").replaceChildren(...["p1", "p2"].map((v) => { const o = el("option", null, v); o.value = v; return o; }));
  await loadGames();
  changeSelection();
}

// 作った・着いた対局を開く（一覧を取り直し、その対局のその席で）
async function openGame(id) {
  remember("game", id);
  await loadGames();
  ui.game = id;
  $("game").value = id;
  ui.live = true;
  changeSelection();
  location.hash = "#/games";
}

function showError(e) {
  // 鍵で守っている対局を、鍵の無いページ・席で開いたとき（localhost と 127.0.0.1 は別のサイトなので、鍵も別に覚える）
  $("detail").textContent = /needs its key/.test(e.message)
    ? t("error.needsKey", { game: ui.game,
      where: t(location.hostname === "localhost" ? "error.needsKey.localhost" : "error.needsKey.fragment") })
    : t("error.prefix", { message: e.message });
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
  $("play").textContent = t("replay.play");
  $("play").classList.remove("on");
}

function startPlay(from = null) {
  if (!timeline) return;
  if (from !== null) ui.pos = from;  // 更新の追いかけ: 前に見ていた最新から
  else if (ui.pos >= ui.cursor) ui.pos = 0;  // 最後まで見ていたら最初から
  ui.live = false;
  ui.playing = true;
  $("play").textContent = t("replay.stop");
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
// ヘッダーの「⋯」: 権利の表記（ダイアログ）・利用規約・プライバシー
function setMore(open) {
  $("moreMenu").hidden = !open;
  $("moreBtn").setAttribute("aria-expanded", String(open));
}
$("moreBtn").onclick = (e) => { e.stopPropagation(); setMore($("moreMenu").hidden); };
$("moreMenu").onclick = (e) => {
  const item = e.target.closest("[role^=menuitem]");
  if (!item) return;
  setMore(false);
  if (item.dataset.open) $(item.dataset.open).showModal();
  if (item.dataset.lang) setLang(item.dataset.lang);
};

// 言語を変えたら、HTML の印（applyDom が済ませた）以外の、描いた文言を描き直す。盤面・操作パネルの中身は後の段階で訳す
onLang(() => {
  seatLabels();
  $("play").textContent = t(ui.playing ? "replay.stop" : "replay.play");
  for (const o of $("game").options) o.textContent = gameLabel(o.value);
  for (const o of $("seat").options) o.textContent = viewing ? VIEW_LABEL(o.value) : o.value === "judge" ? t("seat.judge") : o.value;
  if (site) site.relabel();
  if (ui.view) render();
  if (document.body.classList.contains("log-open")) renderLog();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".more")) setMore(false); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setMore(false);
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || $("ask").open || $("credits").open) return;
  if (e.key === "ArrowLeft") manual(() => step(-1))();
  if (e.key === "ArrowRight") manual(() => step(1))();
  if (e.key === " ") { e.preventDefault(); $("play").click(); }
  if (e.key === "l" || e.key === "L") $("logToggle").click();
});

(async () => {
  try {
    await source.init();
    config = await source.config().catch(() => ({ play: false }));
    if (config.site) await siteSetup();
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
    if (site) {  // 対局が無ければトップから
      const route = routeOf(location.hash) || { page: gameList.length ? "games" : "top", args: [] };
      const go = async (r) => {
        if (r.page === "view") await openView(r.args[0], r.args[1] || null);
        else if (viewing) await closeView();
        await site.show(r.page, r.args);
      };
      window.addEventListener("hashchange", () => go(routeOf(location.hash) || { page: "games", args: [] }).catch(showError));
      await go(route);
    }
    await load();
    listen();
  } catch (e) { showError(e); }
})();
