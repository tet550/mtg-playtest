import test from "node:test";
import assert from "node:assert/strict";
import { Timeline } from "../mtgtable/web/timeline.js";

// 画面の不変の仕様: 時系列（丸ごとの view と差分）からどの時点も同じに戻せること、2つの言語の文言がそろっていること

test("timeline rebuilds every position from keyframes and diffs without changing its input", () => {
  const frames = Array.from({ length: 101 }, (_, i) => i % 25 === 0
    ? { k: { position: i, old: true } } : { d: [["s", ["position"], i], ["d", ["old"]]] });
  const original = structuredClone(frames);
  const timeline = new Timeline({ cursor: 100, frames });
  for (let i = 0; i <= 100; i++) assert.equal(timeline.at(i).position, i);
  assert.equal(timeline.at(1).old, undefined);  // 削除も当てる
  assert.deepEqual(timeline.at(100), timeline.at(1000));
  assert.deepEqual(frames, original);
});

test("both languages have the same text keys and placeholders", async () => {
  const { DICTS } = await import("../mtgtable/web/i18n.js");
  const { ja, en } = DICTS;
  assert.deepEqual(Object.keys(en).sort(), Object.keys(ja).sort());
  const holes = (s) => (typeof s === "string" ? [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort() : []);
  for (const k of Object.keys(ja)) assert.deepEqual(holes(en[k]), holes(ja[k]), k);
});
