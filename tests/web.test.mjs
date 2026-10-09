import test from "node:test";
import assert from "node:assert/strict";
import { Timeline } from "../mtgtable/web/timeline.js";
import { createSource, latestLoader } from "../mtgtable/web/source.js";
import { attackTargets, blockTime, cleanLine, describeOp, hasKept, mulligans, nextStep, planMarks, planned, preview, requestAction, resumeLines, stepName, toRequest, zoneOf } from "../mtgtable/web/play.js";

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
