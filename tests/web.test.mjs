import test from "node:test";
import assert from "node:assert/strict";
import { Timeline } from "../mtgtable/web/timeline.js";
import { createSource, latestLoader } from "../mtgtable/web/source.js";

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
