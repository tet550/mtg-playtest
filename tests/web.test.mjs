import test from "node:test";
import assert from "node:assert/strict";
import { Timeline } from "../mtgtable/web/timeline.js";
import { createSource, latestLoader } from "../mtgtable/web/source.js";
import { attackTargets, blockTime, cleanLine, describeOp, hasKept, manaChoices, mulligans, nextStep, noBlock, planMarks, planned, preview, requestAction, resumeLines, stepName, tapOf, toRequest, zoneOf } from "../mtgtable/web/play.js";
import { lang, setLang } from "../mtgtable/web/i18n.js";

// 画面の文言は日本語で確かめる（node の既定の言語は英語）。英語は個別のテストで切り替えて戻す
setLang("ja");

test("timeline supports seeking, deletion, root replacement and keyframes without mutating input", () => {
  const data = { cursor: 4, frames: [
    { k: { life: 20, cards: ["a"], old: true } },
    { d: [["s", ["life"], 19], ["d", ["old"]]] },
    { d: [["s", [], { life: 18 }]] },
    { k: { life: 17, cards: [] } },
    { d: [["s", ["cards"], ["b"]]] },
  ] };
  const original = structuredClone(data);
  const timeline = new Timeline(data);
  assert.deepEqual(timeline.at(4), { life: 17, cards: ["b"] });
  assert.deepEqual(timeline.at(2), { life: 18 });
  assert.deepEqual(timeline.at(1), { life: 19, cards: ["a"] });
  assert.deepEqual(timeline.at(-1), original.frames[0].k);
  assert.deepEqual(timeline.at(100), timeline.at(4));
  assert.deepEqual(data, original);
});

test("timeline reconstructs positions after the bounded cache evicts them", () => {
  const frames = Array.from({ length: 101 }, (_, i) => i % 25 === 0
    ? { k: { position: i } } : { d: [["s", ["position"], i]] });
  const timeline = new Timeline({ cursor: 100, frames });
  for (let i = 0; i <= 100; i++) assert.equal(timeline.at(i).position, i);
  assert.ok(timeline.cache.size <= 64);
  assert.equal(timeline.at(1).position, 1);
});

test("late responses and errors cannot replace a newer game or seat", async () => {
  const pending = [];
  const load = latestLoader({ load: (game, seat) => new Promise((resolve, reject) => {
    pending.push({ game, seat, resolve, reject });
  }) });
  const judge = load("first", "judge");
  const player = load("second", "p1");
  pending[1].resolve({ timeline: "player", log: [] });
  assert.deepEqual(await player, { timeline: "player", log: [] });
  pending[0].resolve({ timeline: "judge", log: ["private"] });
  assert.equal(await judge, null);
  const stale = load("first", "judge");
  const current = load("second", "p2");
  pending[2].reject(new Error("old error"));
  assert.equal(await stale, null);
  pending[3].reject(new Error("current error"));
  await assert.rejects(current, /current error/);
});

test("live and static sources share a contract; player seats do not request logs", async (t) => {
  const urls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    urls.push(url);
    return { ok: true, json: async () => ({ url }) };
  });
  const live = createSource(false);
  const player = await live.load("a b", "p1");
  assert.deepEqual(urls, ["/api/games/a%20b/timeline?seat=p1"]);
  assert.deepEqual(player.log, []);
  urls.length = 0;
  await createSource(true).load("a b", "judge");
  assert.deepEqual(urls, ["data/a%20b/timeline.json", "data/a%20b/log.json"]);
});

test("subscriptions disconnect and static sites do not create a connection", (t) => {
  const connections = [];
  const previous = globalThis.EventSource;
  t.after(() => {
    if (previous === undefined) delete globalThis.EventSource;
    else globalThis.EventSource = previous;
  });
  globalThis.EventSource = class {
    constructor(url) { this.url = url; connections.push(this); }
    close() { this.closed = true; }
  };
  const states = [], messages = [];
  const close = createSource(false).subscribe("a b", (m) => messages.push(m), (s) => states.push(s));
  connections[0].onopen();
  connections[0].onmessage({ data: '{"cursor":2}' });
  close();
  assert.equal(connections[0].url, "/api/games/a%20b/events");
  assert.equal(connections[0].closed, true);
  assert.equal(connections[0].onmessage, null);
  assert.deepEqual(states, [true, false]);
  assert.deepEqual(messages, [{ cursor: 2 }]);
  createSource(true).subscribe("a", () => {}, () => {})();
  assert.equal(connections.length, 1);
});

test("play helpers: steps and zones", () => {
  assert.equal(stepName({ phase: "main1", step: "main" }), "main1");
  assert.equal(nextStep({ phase: "main2", step: "main" }), "end");
  assert.equal(nextStep({ phase: "ending", step: "cleanup" }), null);
  const v = { zones: { "p1.hand": { cards: [{ id: "#c1" }] }, "p1.library": { known_positions: [{ id: "#c2", index: 0 }] } } };
  assert.equal(zoneOf(v, "#c1"), "p1.hand");
  assert.equal(zoneOf(v, "#c2"), "p1.library");
  assert.equal(zoneOf(v, "#c9"), null);
  assert.equal(describeOp({ op: "damage", target: "p2", amount: 3 }), "p2 に 3 点のダメージ");
});

test("seat key is sent only for its own game and seat; posts report stale writes", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    if (init && init.method === "POST") {
      return { ok: false, status: 409, statusText: "Conflict", json: async () => ({ error: "moved on", stale: true }) };
    }
    return { ok: true, json: async () => [] };
  });
  const source = createSource(false);
  source.setKey({ game: "g", seat: "p1", token: "secret" });
  await source.load("g", "p1");
  assert.deepEqual(calls.map((c) => c.url), ["/api/games/g/timeline?seat=p1", "/api/games/g/log?seat=p1"]);
  assert.ok(calls.every((c) => c.init.headers.Authorization === "Bearer secret"));
  calls.length = 0;
  await source.load("other", "p1");
  assert.deepEqual(calls.map((c) => [c.url, c.init]), [["/api/games/other/timeline?seat=p1", undefined]]);
  await assert.rejects(source.post("g", "request", { seat: "p1", expect: 3 }), (e) => e.stale && e.status === 409);
  const post = calls.pop();
  assert.equal(post.init.headers.Authorization, "Bearer secret");
  assert.deepEqual(JSON.parse(post.init.body), { seat: "p1", expect: 3 });
});

test("pregame choices come from declarations", () => {
  const v = { declarations: [{ turn: 0, player: "p1", kind: "mulligan" }, { turn: 0, player: "p1", kind: "keep" },
    { turn: 0, player: "p2", kind: "mulligan" }, { turn: 3, player: "p2", kind: "keep" }] };
  assert.equal(hasKept(v, "p1"), true);
  assert.equal(hasKept(v, "p2"), false);
  assert.equal(mulligans(v, "p1"), 1);
});

test("draft lines show planned draws as unknown cards and mark the cards they use", () => {
  const lines = [
    { kind: "play_land", cards: ["#c1"], text: "<Forest> (#c1) を出す" },
    { kind: "draw", count: 2, text: "2 枚引く" },
    { kind: "mill", count: 3, targets: ["p2"], text: "p2 のライブラリーの上から 3 枚を墓地に置く" },
    { kind: "attack", cards: ["#c5"], targets: ["p2"], text: "攻撃" },
  ];
  const p = planned(lines, "p1");
  assert.deepEqual(p.hand, { p1: 2 });  // 手札に「？」2 枚（中身は審判の処理の後）
  assert.deepEqual(p.piles, { "p1.library": ["−2 引く"], "p2.library": ["−3 墓地へ"] });
  assert.deepEqual([...planMarks(lines).keys()].sort(), ["#c1", "#c5", "p2"]);
  assert.deepEqual(cleanLine({ kind: "draw", text: "1 枚引く", cards: [], targets: [], count: 1 }),
    { kind: "draw", text: "1 枚引く", count: 1 });  // 空の欄は送らない
});

test("blocks are decided on the board while the defender is waited on", () => {
  const v = {
    turn: { turn: 3, active: "p1", step: "declare_blockers", waiting_on: "p2" },
    combat: { attacks: [{ attacker: "#c1", target: "p2" }], blocks: [] },
    zones: { battlefield: { cards: [{ id: "#c1", controller: "p1" }, { id: "#c9", controller: "p2" }] } },
    requests: { pending: [], asks: [] },
  };
  assert.equal(blockTime(v, "p2"), true);
  assert.equal(blockTime(v, "p1"), false);  // 攻撃側は決めない
  assert.equal(blockTime({ ...v, requests: { pending: [], asks: [{ player: "p2", text: "?" }] } }, "p2"), false);
  assert.equal(blockTime({ ...v, combat: { ...v.combat, blocks: [{ blocker: "#c9", attacker: "#c1" }] } }, "p2"), false);
  assert.equal(blockTime({ ...v, turn: { ...v.turn, step: "combat_damage" } }, "p2"), false);
  const p = planned([{ kind: "block", cards: ["#c9"], targets: ["#c1"], text: "ブロック" }], "p2");
  assert.deepEqual(p.blocks, [{ blocker: "#c9", attacker: "#c1" }]);  // 戦闘の欄に「予定」で出す
});

test("preview shows draft lines on a copy of the board only", () => {
  const v = {
    zones: { "p1.hand": { count: 2, cards: [{ id: "#c1", name: "Forest" }, { id: "#c2", name: "Bolt" }] },
      "p1.library": { count: 30 }, battlefield: { count: 1, cards: [{ id: "#c9", name: "Bear", controller: "p1" }] },
      stack: { count: 0, cards: [] } },
    stack: [], links: [], combat: { attacks: [], blocks: [] },
  };
  const before = JSON.stringify(v);
  const out = preview(v, [
    { kind: "play_land", cards: ["#c1"], text: "出す" },
    { kind: "cast", cards: ["#c2"], targets: ["p2"], text: "唱える" },
    { kind: "attack", cards: ["#c9"], targets: ["p2"], text: "攻撃する" },
    { kind: "draw", count: 1, text: "1 枚引く" },
  ], "p1");
  assert.equal(JSON.stringify(v), before);  // 元の view（卓）は変えない
  assert.deepEqual(out.zones["p1.hand"], { count: 0, cards: [] });
  assert.deepEqual(out.zones.battlefield.cards.map((c) => [c.id, !!c.planned, !!c.tapped]), [["#c9", false, true], ["#c1", true, false]]);
  assert.deepEqual(out.stack[0], { id: "plan1", kind: "spell", card: "#c2", controller: "p1", text: "", planned: true });
  assert.deepEqual(out.links, [{ source: "plan1", kind: "target", targets: ["p2"], planned: true }]);
  assert.deepEqual(out.combat.attacks, [{ attacker: "#c9", target: "p2", planned: true, line: 2 }]);
  assert.equal(out.zones["p1.library"].count, 29);
  assert.equal(preview(v, [], "p1"), v);  // 下書きが無ければそのまま
});

test("preview resolves planned and real stack items and taps an activated source", () => {
  const v = {
    zones: { "p1.hand": { count: 2, cards: [{ id: "#c1", name: "Forest" }, { id: "#c2", name: "Bear" }, { id: "#c3", name: "Bolt" }] },
      "p1.graveyard": { count: 0, cards: [] },
      battlefield: { count: 0, cards: [] },
      stack: { count: 1, cards: [{ id: "#c7", name: "Growth" }] } },
    stack: [{ id: "#s1", kind: "spell", card: "#c7", controller: "p2" }], links: [], combat: { attacks: [], blocks: [] },
    card_kinds: { "#c2": { spell: true, to: "battlefield" }, "#c3": { spell: true, to: "graveyard" }, "#c7": { spell: true, to: "graveyard" } },
  };
  const out = preview(v, [
    { kind: "resolve", targets: ["#s1"], text: "相手の呪文を解決する" },  // 本物の項目は stack の id で
    { kind: "play_land", cards: ["#c1"], text: "出す" },
    { kind: "activate", cards: ["#c1"], text: "<Forest> (#c1) をタップして能力を起動する" },
    { kind: "resolve", cards: ["#c1"], text: "<Forest> の能力を解決する" },  // 予定の項目はカードで
    { kind: "cast", cards: ["#c2"], text: "唱える" },
    { kind: "resolve", cards: ["#c2"], text: "<Bear> を解決する" },
    { kind: "cast", cards: ["#c3"], targets: ["p2"], text: "唱える" },
    { kind: "resolve", cards: ["#c3"], text: "<Bolt> を解決する" },
  ], "p1");
  assert.deepEqual(out.stack, []);
  assert.deepEqual(out.links, []);
  assert.deepEqual(out.zones.battlefield.cards.map((c) => [c.id, !!c.tapped, !!c.planned]), [["#c1", true, true], ["#c2", false, true]]);
  assert.deepEqual(out.zones["p2.graveyard"].cards.map((c) => c.id), ["#c7"]);  // 相手の呪文は相手の墓地へ
  assert.deepEqual(out.zones["p1.graveyard"].cards.map((c) => c.id), ["#c3"]);
  assert.equal(out.zones.stack.count, 0);
  const untapped = preview(v, [
    { kind: "play_land", cards: ["#c1"], text: "出す" },
    { kind: "activate", cards: ["#c1"], text: "<Forest> (#c1) をタップして能力を起動する" },
    { kind: "other", cards: ["#c1"], text: "<Forest> (#c1) をアンタップする" },
  ], "p1");
  assert.equal(untapped.zones.battlefield.cards[0].tapped, false);  // 予定のアンタップも反映する
  const tapped = preview({ ...v, zones: { ...v.zones, battlefield: { count: 1, cards: [{ id: "#c9", name: "Bear", controller: "p1" }] } } },
    [{ kind: "other", cards: ["#c9"], text: "<Bear> (#c9) をタップする" }], "p1");
  assert.equal(tapped.zones.battlefield.cards[0].tapped, true);
});

test("preview takes planned mana spending out of the pool", () => {
  const v = {
    zones: {}, stack: [], links: [], combat: { attacks: [], blocks: [] },
    players: [{ id: "p1", mana: [{ id: "#m1", color: "G", amount: 3 }, { id: "#m2", color: "U", amount: 1 }] }, { id: "p2", mana: [] }],
  };
  const used = preview(v, [{ kind: "other", targets: ["#m1"], count: 2, text: "マナ・プールの {G}（#m1）を 2 使う" }], "p1");
  assert.deepEqual(used.players[0].mana.map((m) => [m.id, m.amount]), [["#m1", 1], ["#m2", 1]]);
  assert.equal(v.players[0].mana[0].amount, 3);  // 卓の view は変えない
  const emptied = preview(v, [{ kind: "other", targets: ["#m1", "#m2"], text: "マナ・プールを空にする" }], "p1");
  assert.deepEqual(emptied.players[0].mana, []);
});

test("a planned step moves the previewed phase and is sent with its target step", () => {
  const v = {
    zones: { battlefield: { count: 1, cards: [{ id: "#c9", name: "Bear", controller: "p1" }] } },
    stack: [], links: [], combat: { attacks: [], blocks: [] },
    turn: { turn: 3, active: "p1", phase: "main1", step: "main", priority: "p1" },
  };
  const step = { kind: "step", to: "beginning_of_combat", text: "戦闘開始 へ進む" };
  assert.deepEqual(cleanLine(step), step);  // to も送る
  assert.equal(cleanLine({ kind: "other", to: "main2", text: "x" }).to, undefined);  // step の行だけ
  const out = preview(v, [step, { kind: "attack", cards: ["#c9"], targets: ["p2"], text: "攻撃する" }], "p1");
  assert.deepEqual([out.turn.phase, out.turn.step, out.turn.planned], ["combat", "beginning_of_combat", true]);
  assert.equal(out.combat.attacks.length, 1);
  const after = preview(v, [step, { kind: "attack", cards: ["#c9"], targets: ["p2"], text: "攻撃する" },
    { kind: "step", to: "main2", text: "メイン2 へ進む" }], "p1");
  assert.deepEqual([after.turn.phase, after.turn.step], ["main2", "main"]);
  assert.equal(after.combat.attacks.length, 0);  // 戦闘を抜けたら予定の攻撃は外す
  assert.equal(v.turn.step, "main");  // 元の view は変えない
});

test("the draft's last line decides what happens after the request", () => {
  const mine = { active: "p1", priority: "p1", step: "main", phase: "main1", waiting_on: "p1" };
  const cast = { kind: "cast", text: "<Bolt> (#c1) を唱える", cards: ["#c1"], targets: [] };
  const end = { kind: "then", then: "end_turn", text: "ターン終了" };
  const step = { kind: "step", text: "メイン2 へ進む", to: "main2" };
  assert.deepEqual(toRequest([cast, end], mine, "p1"), { plan: [cleanLine(cast)], then: "end_turn" });
  assert.deepEqual(toRequest([cast, step], mine, "p1"), { plan: [cleanLine(cast)], then: "main2" });
  assert.deepEqual(toRequest([step, cast], mine, "p1").then, "continue");  // 途中のステップは行のまま
  assert.deepEqual(toRequest([end, cast], mine, "p1").plan[0], { kind: "other", text: "ターン終了" });
  const theirs = { ...mine, active: "p2" };
  assert.equal(toRequest([cast], theirs, "p1").then, "pass");  // 相手のターンはパス
  assert.equal(toRequest([], { ...theirs, step: "cleanup", priority: null }, "p1").then, "turn_start");
  // 下書きが空: パスの宣言（審判を通さない）か、送れない
  assert.equal(requestAction([], "", theirs, "p1", 0).kind, "pass");
  assert.equal(requestAction([], "", mine, "p1", 1).kind, "pass");  // 自分のターンでも、相手の呪文がスタックにあれば
  // 自分のターンで何も無い: 次のステップへ（終了ステップならターン終了）。全員パスで優先権が無くても押せる
  assert.equal(requestAction([], "", mine, "p1", 0).then, "beginning_of_combat");
  assert.equal(requestAction([], "", { ...mine, step: "declare_blockers", phase: "combat", priority: null }, "p1", 0).then, "combat_damage");
  assert.equal(requestAction([], "", { ...mine, step: "end", phase: "ending" }, "p1", 0).then, "end_turn");
  assert.equal(requestAction([], "", { ...theirs, priority: "p2" }, "p1", 0), null);  // 相手のターンで優先権が無い
  assert.equal(requestAction([], "メモ", mine, "p1", 0).kind, "request");
  assert.equal(requestAction([cast], "", { ...theirs, priority: "p2", waiting_on: "p2" }, "p1", 0), null);  // 相手が考えている間は送れない
  assert.equal(requestAction([cast], "", { ...mine, turn: 0, waiting_on: "p2" }, "p1", 0).kind, "request");  // ゲーム前の申し出は出せる
  assert.equal(requestAction([end], "", mine, "p1", 0).label, "審判に依頼（その後: ターン終了）");
  assert.equal(requestAction([cast, step], "", mine, "p1", 0).label, "審判に依頼（その後: メイン2へ）");
});

test("the rest of an interrupted plan goes back to the draft with its 'then' last", () => {
  const cast = { kind: "cast", text: "<Bolt> (#c1) を唱える", cards: ["#c1"] };
  assert.deepEqual(resumeLines({ lines: [cast], then: "end_turn" }),
    [cleanLine(cast), { kind: "then", then: "end_turn", text: "ターン終了" }]);
  assert.deepEqual(resumeLines({ lines: [cast], then: "main2" }).at(-1), { kind: "step", to: "main2", text: "メイン2 へ進む" });
  assert.deepEqual(resumeLines({ lines: [cast], then: "continue" }), [cleanLine(cast)]);
  // 戻した行をそのまま送ると、元の「その後」になる
  const turn = { active: "p1", priority: "p1", step: "main", phase: "main1", waiting_on: "p1" };
  assert.equal(toRequest(resumeLines({ lines: [cast], then: "end_turn" }), turn, "p1").then, "end_turn");
});

test("attack targets are the opponent, their planeswalkers and my battles", () => {
  const v = { zones: { battlefield: { cards: [
    { id: "#c1", owner: "p1", controller: "p1" },
    { id: "#c2", owner: "p2", controller: "p2", attackable: "planeswalker" },
    { id: "#c3", owner: "p1", controller: "p1", attackable: "planeswalker" },
    { id: "#c4", owner: "p1", controller: "p1", attackable: "battle" },
    { id: "#c5", owner: "p2", controller: "p2", attackable: "battle" },
  ] } } };
  assert.deepEqual(attackTargets(v, "p1", "p2"), ["p2", "#c2", "#c4"]);
  assert.deepEqual(attackTargets({ zones: { battlefield: {} } }, "p1", "p2"), ["p2"]);
});

test("deck check results become lines with line numbers, errors first", async () => {
  const { checkLines, summary } = await import("../mtgtable/web/decks.js");
  const r = { ok: false, main: 59, sideboard: 0,
    errors: [{ line: null, message: "メインデッキが 59 枚です" }, { line: 3, message: "見つかりません" }],
    warnings: [{ line: 5, message: "分かりませんでした" }] };
  assert.deepEqual(checkLines(r).map((l) => [l.kind, l.text]), [
    ["error", "メインデッキが 59 枚です"], ["error", "3 行目: 見つかりません"], ["warning", "5 行目: 分かりませんでした"]]);
  assert.equal(summary(r), "メイン 59 枚・サイドボード 0 枚。直す所が 2 件あります");
  assert.equal(summary({ ok: true, main: 60, sideboard: 15, errors: [] }), "メイン 60 枚・サイドボード 15 枚。登録できます");
  assert.deepEqual(checkLines(null), []);
});

test("site pages come from the hash", async () => {
  const { pageOf } = await import("../mtgtable/web/site.js");
  assert.equal(pageOf("#/decks"), "decks");
  assert.equal(pageOf("#/top"), "top");
  assert.equal(pageOf("#/nope"), null);
  assert.equal(pageOf("#key=abc"), null);  // 招待の URL の鍵は画面の切り替えではない
  assert.equal(pageOf(""), null);
});

test("idle notice only for the other human's turn, with a claim after the limit", async () => {
  const { idleState } = await import("../mtgtable/web/play.js");
  const idle = { seconds: 400, notice: 300, limit: 1800, humans: ["p1", "p2"], at: 1000 };
  assert.deepEqual(idleState(idle, "p2", "p1", 1000), { who: "p2", minutes: 6, canClaim: false, left: 24 });
  assert.equal(idleState(idle, "p1", "p1", 1000), null);  // 自分の番
  assert.equal(idleState(idle, "judge", "p1", 1000), null);  // 審判の番
  assert.equal(idleState({ ...idle, humans: ["p1"] }, "p2", "p1", 1000), null);  // AI の番
  assert.equal(idleState({ ...idle, seconds: 100 }, "p2", "p1", 1000), null);  // まだ知らせない
  assert.equal(idleState(idle, "p2", "p1", 1000 + 1500 * 1000).canClaim, true);  // 受け取ってから時間が進む
  assert.equal(idleState(null, "p2", "p1", 1000), null);
});

test("invite links and routes", async () => {
  const { inviteURL, inviteState } = await import("../mtgtable/web/lobby.js");
  const { routeOf } = await import("../mtgtable/web/site.js");
  const url = inviteURL("https://mtg.example", "ab12", "t/k+n");
  assert.equal(url, "https://mtg.example/#/join/ab12/t%2Fk%2Bn");
  assert.deepEqual(routeOf(new URL(url).hash), { page: "join", args: ["ab12", "t/k+n"] });
  assert.deepEqual(routeOf("#/new"), { page: "new", args: [] });
  assert.equal(inviteState({ game: "g1" }), "対局になった");
  assert.equal(inviteState({ game: null, expired: true }), "期限切れ");
  assert.equal(inviteState({ game: null, expired: false }), "相手待ち");
});

test("history helpers: view links, matchups and the view route", async () => {
  const { viewURL, matchup, when, RESULT } = await import("../mtgtable/web/history.js");
  const { routeOf, BOARD_PAGES } = await import("../mtgtable/web/site.js");
  const url = viewURL("https://mtg.example", "g1", "s/k");
  assert.equal(url, "https://mtg.example/#/view/g1/s%2Fk");
  assert.deepEqual(routeOf(new URL(url).hash), { page: "view", args: ["g1", "s/k"] });
  assert.deepEqual(routeOf("#/view/g1"), { page: "view", args: ["g1"] });
  assert.equal(viewURL("https://mtg.example", "g1"), "https://mtg.example/#/view/g1");
  assert.ok(BOARD_PAGES.includes("view") && BOARD_PAGES.includes("games"));
  assert.equal(matchup([{ id: "p1", name: "green" }, { id: "p2", name: "piza" }]), "green（p1） 対 piza（p2）");
  assert.equal(when("2026-10-09T16:02:43"), "2026-10-09 16:02");
  assert.equal(RESULT.won, "勝ち");
});

test("legal pages show the operator and contact, or say they are unset", async () => {
  const { termsSections, privacySections } = await import("../mtgtable/web/legal.js");
  const flat = (sections) => sections.flatMap(([h, items]) => [h, ...items]).join("\n");
  const set = flat(termsSections({ operator: "山田", contact: "mail@example.com" }));
  assert.ok(set.includes("運営者: 山田") && set.includes("連絡先: mail@example.com"));
  assert.ok(flat(privacySections({})).includes("連絡先: （未設定）"));
  assert.ok(flat(privacySections({})).includes("mtg_owner"));  // 使う Cookie を書いている
  const { routeOf } = await import("../mtgtable/web/site.js");
  assert.equal(routeOf("#/terms").page, "terms");
  assert.equal(routeOf("#/privacy").page, "privacy");
});

test("basic mana notes become the choices of mana to tap for; others stay as the note", () => {
  const pick = (text) => manaChoices([{ text }]).map((c) => c.mana || `note: ${c.note}`);
  assert.deepEqual(pick("mana: {G} or {U}"), ["{G}", "{U}"]);
  assert.deepEqual(pick("mana: {W}"), ["{W}"]);
  assert.deepEqual(pick("mana: {C}{C}"), ["{C}{C}"]);
  assert.deepEqual(pick("mana: {W}, {U}, or {B}"), ["{W}", "{U}", "{B}"]);
  assert.deepEqual(pick("mana: {C}, or {T} + 1 life: any color"), ["note: {C}, or {T} + 1 life: any color"]);
  assert.deepEqual(pick("mana: {C}（基本土地があれば {R} or {G}）"), ["note: {C}（基本土地があれば {R} or {G}）"]);
  assert.deepEqual(pick("+1/+1 until end of turn"), []);
});

test("tapping for mana from the menu taps the land in the preview", () => {
  const v = { turn: {}, players: [], links: [], stack: [], combat: { attacks: [], blocks: [] },
    zones: { battlefield: { cards: [{ id: "#c1", name: "Forest", controller: "p1", land: true }] } } };
  const out = preview(v, [cleanLine({ kind: "other", text: "<Forest> (#c1) をタップする（{G} を出す）", cards: ["#c1"] })], "p1");
  assert.equal(out.zones.battlefield.cards[0].tapped, true);
});

test("the preview reads tap and no_block from the line, not from its text", () => {
  // 文は画面の言語で変わる（英語の文でも同じに動く）。値の無い古い行だけ、日本語の文から読む
  const v = { turn: {}, players: [], links: [], stack: [], combat: { attacks: [], blocks: [] },
    zones: { battlefield: { cards: [{ id: "#c1", name: "Forest", controller: "p1", land: true },
      { id: "#c2", name: "Bear", controller: "p1" }, { id: "#c3", name: "Elf", controller: "p1", tapped: true }] } } };
  const out = preview(v, [
    cleanLine({ kind: "activate", cards: ["#c1"], text: "Tap <Forest> (#c1) to activate", tap: "tap" }),
    cleanLine({ kind: "attack", cards: ["#c2"], targets: ["p2"], text: "<Bear> (#c2) attacks p2", tap: "none" }),
    cleanLine({ kind: "other", cards: ["#c3"], text: "Untap <Elf> (#c3)", tap: "untap" }),
  ], "p1");
  assert.deepEqual(out.zones.battlefield.cards.map((c) => [c.id, !!c.tapped]), [["#c1", true], ["#c2", false], ["#c3", false]]);
  assert.equal(preview(v, [{ kind: "attack", cards: ["#c2"], targets: ["p2"], text: "<Bear> (#c2) attacks p2" }], "p1")
    .zones.battlefield.cards[1].tapped, true);  // 攻撃は、tap: "none" でなければタップ
  assert.deepEqual(cleanLine({ kind: "other", text: "x", tap: "sideways", no_block: false }), { kind: "other", text: "x" });
  assert.deepEqual(cleanLine({ kind: "other", text: "No blocks", no_block: true }), { kind: "other", text: "No blocks", no_block: true });
  // 古い行（値が無い）
  assert.equal(tapOf({ kind: "activate", text: "<Forest> (#c1) をタップして能力を起動する" }), "tap");
  assert.equal(tapOf({ kind: "attack", text: "<Bear> で p2 を攻撃する（タップしない）" }), "none");
  assert.equal(tapOf({ kind: "other", text: "<Elf> をアンタップする" }), "untap");
  assert.equal(tapOf({ kind: "other", text: "<Elf> に Note を付ける" }), null);
  assert.equal(noBlock({ kind: "other", text: "No blocks", no_block: true }), true);
  assert.equal(noBlock({ kind: "other", text: "ブロックしない" }), true);
  assert.equal(noBlock({ kind: "other", text: "No blocks" }), false);
});

test("both languages have the same text keys and placeholders", async () => {
  const { DICTS } = await import("../mtgtable/web/i18n.js");
  const { ja, en } = DICTS;
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ja).sort());  // 片方だけのキーを作らない
  const holes = (s) => (typeof s === "string" ? [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort() : []);
  for (const k of Object.keys(ja)) assert.deepEqual(holes(en[k]), holes(ja[k]), k);
});

test("the language comes from the saved choice, then the browser, then English", async () => {
  const { detect } = await import("../mtgtable/web/i18n.js");
  assert.equal(detect("en", ["ja-JP"]), "en");
  assert.equal(detect(null, ["ja-JP", "en-US"]), "ja");
  assert.equal(detect("fr", ["fr-FR", "en-GB"]), "en");
  assert.equal(detect(null, ["de-DE"]), "en");
  assert.equal(detect(null, []), "en");
});

test("t fills placeholders and falls back to the other language, then the key", async () => {
  const { t, DICTS, lang } = await import("../mtgtable/web/i18n.js");
  const other = lang() === "ja" ? "en" : "ja";
  assert.equal(t("header.playing", { seat: "p1" }), DICTS[lang()]["header.playing"].replace("{seat}", "p1"));
  DICTS[other]["test.only"] = "only {x}";
  try { assert.equal(t("test.only", { x: 1 }), "only 1"); } finally { delete DICTS[other]["test.only"]; }
  assert.equal(t("no.such.key"), "no.such.key");
});

test("play texts follow the screen language while line marks do not", () => {
  const mine = { turn: 3, active: "p1", step: "main", phase: "main1", priority: "p1", waiting_on: "p1" };
  const end = { kind: "then", then: "end_turn", text: "ターン終了" };
  setLang("en");
  try {
    assert.equal(lang(), "en");
    assert.equal(requestAction([end], "", mine, "p1", 0).label, "Send to the judge (then: End turn)");
    assert.deepEqual(resumeLines({ lines: [], then: "main2" }).at(-1), { kind: "step", to: "main2", text: "Go to Main 2" });
    assert.equal(describeOp({ op: "damage", target: "p2", amount: 3 }), "3 damage to p2");
    assert.deepEqual(planned([{ kind: "draw", count: 2 }], "p1").piles, { "p1.library": ["−2 draw"] });
  } finally {
    setLang("ja");
  }
  assert.equal(requestAction([end], "", mine, "p1", 0).label, "審判に依頼（その後: ターン終了）");
});
