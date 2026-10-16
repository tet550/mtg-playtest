// 手元のサイト（対局の盤面と、トップ・デッキ・対局を作る・履歴の画面）の起動・画面操作・更新を調停する。
import { $, el, noSelect } from "./dom.js";
import { createSource, latestLoader } from "./source.js";
import { Timeline } from "./timeline.js";
import { createRenderer } from "./render.js";
import { createPlay, toast } from "./play.js";
import { createSite, routeOf } from "./site.js";
import { applyDom, onLang, setLang, t } from "./i18n.js";
import { canRecord, createRecorder } from "./record.js";

applyDom();  // 今の言語の文言を HTML の印に入れる（描く前に）

// play: 席の鍵を持って対局しているときの {seat}（無ければ観戦）。marks: 組み立て中の Act で選んだもの
const ui = { game: null, seat: "judge", live: true, pos: 0, cursor: 0, view: null, log: [], names: {},
  images: true, motion: true, open: new Set(), attacking: new Set(), blocking: new Set(), sides: {}, timer: null,
  play: null, marks: new Map() };
let config = { host: false };
const source = createSource();
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

let mySeats = {};  // 手元のサイトで、ホストの席 {対局: 席}（鍵が無くても手元のブラウザなので対局できる）
let gameList = [];
let viewing = null;  // 再生（#/view/<対局>）: {game, finished, views}
const VIEW_LABEL = (v) => (v === "judge" ? t("seat.all") : v);
let site = null;  // 手元のサイトの画面の切り替え（トップ・デッキ・対局）
const guest = () => !config.host;  // 招待で着いたゲスト（外のブラウザ。席の鍵だけで入る）

// ゲストの対局: 一覧は見せないので、このブラウザが鍵を覚えている対局
function rememberedGames() {
  const out = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("mtgtable.key.")) out.push(k.slice("mtgtable.key.".length));
    }
  } catch (_) { /* 保存できない */ }
  return out;
}
function keyOf(game) {
  let k = null;
  try { k = JSON.parse(recall("key." + game) || "null"); } catch (_) { k = null; }
  if (mySeats[game] && (!k || k.seat !== mySeats[game])) k = { seat: mySeats[game], token: null };
  return k;
}

// 選んだ対局の鍵を持っていれば、その席で対局する（席は固定）。無ければ観戦
function applySeatMode() {
  // 再生・観戦（#/view）では、自分の席でも対局が終わっていれば操作しない（どの視点でも見られる）
  const k = ui.game && !(viewing && viewing.finished) ? keyOf(ui.game) : null;
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
  $("rec").hidden = !(viewing && !k && canRecord());  // 録画は再生（#/view）で、操作していないときだけ
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
  if (guest()) return t("game.option.seat", { id });  // ゲストが鍵で入っている対局（一覧が無いので、ターンは出せない）
  return t(viewing && viewing.finished ? "game.option.replay" : "game.option.watch", { id });
}

// ---------------------------------------------------------------- 読み込み

async function loadGames() {
  const games = await source.games();
  gameList = games;
  mySeats = Object.fromEntries(games.filter((g) => g.my_seat).map((g) => [g.id, g.my_seat]));
  const ids = games.map((g) => g.id);
  if (guest()) ids.push(...rememberedGames().filter((id) => !ids.includes(id)));
  const sel = $("game");
  sel.replaceChildren(...ids.map((id) => {
    const o = el("option", null, gameLabel(id));
    o.value = id;
    return o;
  }));
  const saved = params.get("game") || recall("game");
  ui.game = ids.includes(saved) ? saved : ids[0];
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
  ui.idle = result.timeline.idle ? { ...result.timeline.idle, at: Date.now() } : null;  // 時間切れの知らせ（手元のサイト）
  ui.tutorial = result.timeline.tutorial || null;  // チュートリアルの今の段の案内
  ui.log = result.log;
  ui.cursor = timeline.cursor;
  if (follow) return startPlay(from);
  if (ui.playing) return;  // 描き直すと、動いている途中の演出（解決して飛ぶカードなど）が止まって消える
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
  recorder.stop();
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
  delete $("log").dataset.cur;
  for (const id of ["fl-detail", "fl-stack", "combatbox"]) $(id).hidden = true;
  load().catch(showError);
  listen();
}

// 手元のサイト: ヘッダーの画面の切り替え。全体の視点（judge）は、終わった対局の再生（#/view）でだけ選べる
function siteSetup() {
  $("seat").replaceChildren(...[...$("seat").options].filter((o) => o.value !== "judge"));
  site = createSite(source, { toast, games: () => gameList, openGame, config: () => config });
}

// 再生: 見られる視点（サーバーの access）を席の選択に出し、その対局を開く
async function openView(game) {
  const a = await source.access(game);
  viewing = { game, finished: a.finished, views: a.views };
  source.setViewing({ game });
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

// 作った・着いた対局を開く（一覧を取り直し、その対局のその席で）。ゲストは着いたときの席の鍵を覚える
async function openGame(id, key) {
  if (key) remember("key." + id, JSON.stringify({ seat: key.seat, token: key.token }));
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

// 盤面に見えるものだけの印。宣言・優先権・依頼など、盤面の外の変化しかない Act は自動再生で待たずに通り過ぎる
function boardKey(v) {
  const { turn, active, phase, step } = v.turn || {};
  return JSON.stringify([v.zones, v.players, v.stack, v.combat, v.links, turn, active, phase, step]);
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
  const interval = () => Number($("speed").value);
  const step = () => {
    if (ui.pos >= ui.cursor) {  // 最後まで来たら止めて Live に戻る
      stopPlay();
      ui.live = true;
      return show();
    }
    const from = ui.pos;
    const before = boardKey(viewAt(from));
    do ui.pos = Math.min(nextPos(), ui.cursor);
    while (ui.pos < ui.cursor && boardKey(viewAt(ui.pos)) === before);
    ui.deltaBase = { pos: ui.pos, from };  // 自動再生では、1回前に表示した位置と比べる（宣言の吹き出しが新しく出たか）
    ui.live = false;
    ui.animate = { duration: Math.min(450, interval() * 0.7) };
    show();
    if (ui.timer !== null) ui.timer = setTimeout(step, interval());
  };
  ui.timer = setTimeout(step, 0);
}

const manual = (f) => (...args) => { stopPlay(); return f(...args); };

// 録画（再生の画面）: 今の位置から自動再生して、盤面を動画に録る
const recorder = createRecorder({ ui, target: document.querySelector(".board"), startPlay: () => startPlay(), stopPlay, toast,
  fileName: (from, ext) => `mtgtable-${ui.game}-${ui.seat}-${from}.${ext}` });

// ---------------------------------------------------------------- 小窓と Log

// 小窓: 見出しをドラッグで盤面の中を移動（位置は覚える）、– で畳む、× で閉じる
const floatClamps = [];  // 盤面の大きさが変わったら（Log の開閉・窓の大きさ）、動かした小窓を盤面の中に戻す
function clampFloats() { floatClamps.forEach((f) => f()); }
addEventListener("resize", () => { placeSpeech(); clampFloats(); });

// 置き場所は、近い方の端（左か右・上か下）から小窓の左上までの距離で覚える。窓の大きさが変わっても右下に置いた小窓は右下に残り、
// 狭くして詰めた位置は覚えた位置を変えない（広げると戻る）
function setupFloat(panel) {
  const key = "float." + panel.id;
  const board = panel.parentElement;
  // 画面の下に留めている間（狭い画面の操作パネル。CSS の --docked）は、位置を当てず、ドラッグもしない
  const docked = () => getComputedStyle(panel).getPropertyValue("--docked").trim() === "1";
  const area = () => getComputedStyle(panel).position === "fixed"
    ? { left: 0, top: 0, width: document.documentElement.clientWidth, height: document.documentElement.clientHeight }
    : board.getBoundingClientRect();
  let want = null;  // ドラッグで決めた位置 { x, y, right, bottom }。無ければ CSS の既定の位置
  try {
    const saved = JSON.parse(recall(key) || "null");
    if (saved) panel.classList.toggle("min", !!saved.min);
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) want = { x: saved.x, y: saved.y, right: !!saved.right, bottom: !!saved.bottom };
  } catch (_) { /* 覚えた位置が無くても動く */ }
  const place = () => {
    if (!want || panel.hidden || !panel.offsetWidth || docked()) return;
    const b = area(), w = panel.offsetWidth, h = panel.offsetHeight;
    if (b.width < 60 || b.height < 60) return;  // 盤面がまだ描かれていない
    const x = want.right ? b.width - w - want.x : want.x;
    const y = want.bottom ? b.height - want.y : want.y;
    // 入りきらなければ画面の中に詰める（下にはみ出すなら上へ。それでも入らなければ見出しを見せる）
    const nx = Math.max(0, Math.min(x, b.width - w));
    const ny = Math.max(0, Math.min(y, b.height - h));
    Object.assign(panel.style, { left: `${nx}px`, top: `${ny}px`, right: "auto", bottom: "auto" });
  };
  const anchor = (nx, ny) => {
    const b = area(), w = panel.offsetWidth, h = panel.offsetHeight;
    nx = Math.max(0, Math.min(nx, b.width - w));
    ny = Math.max(0, Math.min(ny, b.height - h));
    const right = nx + w / 2 > b.width / 2, bottom = ny + h / 2 > b.height / 2;
    return { x: right ? b.width - w - nx : nx, y: bottom ? b.height - ny : ny, right, bottom };
  };
  const settle = () => { place(); avoidPlayPanel(); };
  floatClamps.push(settle);
  // 中身が変わって小窓の大きさが変わったとき・盤面の大きさが変わったときも詰め直す
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(settle);
    ro.observe(panel);
    ro.observe(board);
  }
  const save = () => remember(key, JSON.stringify({ ...(want || {}), min: panel.classList.contains("min") }));
  panel.querySelector(".fmin").onclick = () => { panel.classList.toggle("min"); save(); settle(); };
  const close = panel.querySelector(".fclose");
  if (close) close.onclick = () => { panel.hidden = true; };
  panel.querySelector(".fhead").addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || docked()) return;
    const pb = panel.getBoundingClientRect(), bb = area();
    const start = { x: e.clientX, y: e.clientY, left: pb.left - bb.left, top: pb.top - bb.top };
    const move = (ev) => { want = anchor(start.left + ev.clientX - start.x, start.top + ev.clientY - start.y); settle(); };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); noSelect(false); save(); };
    noSelect(true);
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    e.preventDefault();
  });
}
// 操作パネル（画面に固定）が盤面の右の帯（スタックの置き場所）に重なるときは、スタックをよける。
// パネルの上に十分な高さがあれば、スタックの下端をパネルのすぐ上に上げる（--stackb・--stackmaxh。戦闘の窓は動かない）。
// 無ければ、重なる幅（--panelw）だけスタックと戦闘の窓を左へ寄せる。畳んで見出しだけなら下に収まるので、どちらもしない
const STACK_BAND = 340;  // スタックの幅と右の余白（style.css の .stackbox）
const STACK_MIN_H = 150;  // パネルの上に置くのに要る高さ（1項目と見出し）
function avoidPlayPanel() {
  const panel = $("fl-play"), board = document.querySelector(".board");
  if (!panel || !board) return;
  const vars = { "--panelw": null, "--stackb": null, "--stackmaxh": null };
  if (!panel.hidden && getComputedStyle(panel).getPropertyValue("--docked").trim() !== "1") {
    const p = panel.getBoundingClientRect(), b = board.getBoundingClientRect();
    const stackBottom = b.bottom - (b.height / 2 - 74);  // 既定のスタックの下端（.stackbox の bottom）
    if (p.width && p.left < b.right && p.right > b.right - STACK_BAND && p.top < stackBottom && p.bottom > b.top) {
      const above = p.top - b.top - 16;
      const gap = b.right - p.left + 6;
      if (above >= STACK_MIN_H) {
        vars["--stackb"] = `${Math.round(b.bottom - p.top + 8)}px`;
        vars["--stackmaxh"] = `${Math.round(above)}px`;
      } else if (gap <= b.width - STACK_BAND - 40) {  // 寄せても入らない（パネルを盤面の真ん中へ動かした）ときは寄せない
        vars["--panelw"] = `${Math.round(gap)}px`;
      }
    }
  }
  for (const [k, v] of Object.entries(vars)) {
    if (v) board.style.setProperty(k, v); else board.style.removeProperty(k);
  }
}
document.querySelectorAll(".float").forEach(setupFloat);

// Log: 開発者ツールのように右に寄せて開閉し、境目をドラッグで幅を変える（開閉と幅は覚える）
function setLog(open) {
  document.body.classList.toggle("log-open", open);
  $("logToggle").classList.toggle("on", open);
  remember("logOpen", open ? "1" : "0");
  if (open) { delete $("log").dataset.cur; renderLog(); }  // 開いたら今の位置へ寄せる
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
    noSelect(false);
    placeSpeech();
    clampFloats();
    remember("logWidth", getComputedStyle(document.documentElement).getPropertyValue("--logw").trim());
  };
  noSelect(true);
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
  recorder.relabel();
  if (ui.view) render();
  if (document.body.classList.contains("log-open")) renderLog();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".more")) setMore(false); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setMore(false);
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || document.body.classList.contains("sitepage") || document.querySelector("dialog[open]")) return;
  if (e.key === "ArrowLeft") manual(() => step(-1))();
  if (e.key === "ArrowRight") manual(() => step(1))();
  if (e.key === " ") { e.preventDefault(); $("play").click(); }
  if (e.key === "l" || e.key === "L") $("logToggle").click();
});

(async () => {
  try {
    config = await source.config().catch(() => ({ host: false }));
    siteSetup();
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
      const route = routeOf(location.hash) || { page: gameList.length || guest() ? "games" : "top", args: [] };
      const go = async (r) => {
        if (guest() && r.page !== "join") r = { page: "games", args: [] };  // ゲストは招待と自分の席の盤面だけ
        if (r.page === "view") await openView(r.args[0]);
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
