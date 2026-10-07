// カードの動き（タップ・移動・束への出し入れ）。描画の前後でカードの位置を比べ、写しを重ねて動かす（FLIP）。
// 本物の要素は行や小窓のはみ出しで切られるので、画面全体に重ねた層で写しを動かし、その間は本物を隠す。
// view（公開範囲を適用済み）に無いカードは動かさないので、見えない情報は漏れない。

// 追う対象: 両 Player の盤面・手札・下に差し込んだ追放と、スタックの呪文（戦闘の欄・開いた束の一覧は同じカードの写し）
const TRACKED = "#top [data-ids], #bottom [data-ids], #stack .sitem:not(.ability) [data-ids]";
const MAX_JOBS = 80;  // 一度にこれより多く動くときは（大きな巻き戻しなど）動かさない
const EASE = "cubic-bezier(.2, .7, .2, 1)";
const LISTS = ["cards", "known", "known_positions", "known_unordered"];

function center(r) {
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

// view の中でカードがどの領域にあるか（見えないところにあれば null）
function zoneOf(view, id) {
  for (const [key, z] of Object.entries(view.zones)) {
    for (const list of LISTS) {
      const c = (z[list] || []).find((x) => x.id === id);
      if (c) return { key, card: c };
    }
  }
  return null;
}

// 領域を、画面の束（ライブラリー・墓地・追放）か手札の欄の名前に
function placeKey(key, card) {
  if (key === "exile") return card.owner ? `${card.owner}.exile` : null;
  return /\.(library|graveyard|hand)$/.test(key) ? key : null;
}

export function createMotion() {
  const layer = document.createElement("div");
  layer.id = "motionlayer";
  document.body.append(layer);
  let hidden = [];
  let animations = [];

  // 今の画面のカードと束の位置
  function capture() {
    const cards = new Map();
    for (const e of document.querySelectorAll(TRACKED)) {
      if (e.closest(".zone.opened")) continue;
      const rect = e.getBoundingClientRect();
      if (!rect.width) continue;
      const item = { el: e, rect, tapped: e.classList.contains("tapped"), ids: e.dataset.ids.split(" ") };
      for (const id of item.ids) if (!cards.has(id)) cards.set(id, item);
    }
    const places = new Map();
    for (const e of document.querySelectorAll("[data-zone]")) {
      const rect = (e.querySelector(".pstack") || e).getBoundingClientRect();
      if (rect.width) places.set(e.dataset.zone, rect);
    }
    return { cards, places };
  }

  // 動いている途中なら、すぐ終わらせる（次の描画の前に呼ぶ）
  function stop() {
    for (const a of animations) a.cancel();
    animations = [];
    for (const e of hidden) e.style.visibility = "";
    hidden = [];
    layer.replaceChildren();
  }

  // 要素の写しを、その位置に重ねる。祖先のクラスで決まる大きさは測った値で固定する
  function ghost(e, rect) {
    const g = e.cloneNode(true);
    g.classList.remove("sel");
    g.style.visibility = "";
    Object.assign(g.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, margin: "0",
      width: `${e.offsetWidth}px`, height: `${e.offsetHeight}px`, boxSizing: "border-box" });
    const box = e.querySelector(".artbox"), gbox = g.querySelector(".artbox");
    if (box && gbox) {
      Object.assign(gbox.style, { width: `${box.offsetWidth}px`, height: `${box.offsetHeight}px` });
      const img = box.querySelector("img.art"), gimg = gbox.querySelector("img.art");
      if (img && gimg) Object.assign(gimg.style, { width: `${img.offsetWidth}px`, height: `${img.offsetHeight}px` });
    }
    layer.append(g);
    return g;
  }

  function run(target, frames, duration, done) {
    const a = target.animate(frames, { duration, easing: EASE, fill: "both" });
    animations.push(a);
    a.onfinish = () => { a.cancel(); done && done(); };
    return a;
  }

  // 本物を隠して写しを動かし、終わったら本物に戻す
  function fly(now, frames, duration) {
    const g = ghost(now.el, now.rect);
    now.el.style.visibility = "hidden";
    hidden.push(now.el);
    run(g, frames, duration, () => { g.remove(); now.el.style.visibility = ""; });
  }

  // 前の位置・向きから今の位置へ（タップは 90° 回す）
  function move(was, now, duration) {
    const a = center(was.rect), b = center(now.rect);
    const turn = was.tapped === now.tapped ? 0 : now.tapped ? -90 : 90;
    const [sx, sy] = turn
      ? [was.rect.height / now.rect.width, was.rect.width / now.rect.height]
      : [was.rect.width / now.rect.width, was.rect.height / now.rect.height];
    fly(now, [{ transform: `translate(${a.x - b.x}px, ${a.y - b.y}px) rotate(${turn}deg) scale(${sx}, ${sy})` },
      { transform: "none" }], duration);
  }

  // 束（ライブラリーなど）から出てくる
  function enter(now, from, duration) {
    const a = center(from), b = center(now.rect);
    const s = Math.min(from.width / now.rect.width, from.height / now.rect.height);
    fly(now, [{ transform: `translate(${a.x - b.x}px, ${a.y - b.y}px) scale(${s})`, opacity: 0.4 },
      { transform: "none", opacity: 1 }], duration);
  }

  // どこからか分からないもの（トークンなど）は、その場に現れる
  function appear(now, duration) {
    run(now.el, [{ transform: "scale(.6)", opacity: 0 }, { transform: "none", opacity: 1 }], duration);
  }

  // 画面から消えるカードは、写しを行き先の束まで飛ばす（行き先が見えなければ、その場で消える）
  function leave(was, to, duration) {
    const g = ghost(was.el, was.rect);
    const a = center(was.rect);
    const end = to
      ? `translate(${center(to).x - a.x}px, ${center(to).y - a.y}px) scale(${Math.min(to.width / was.rect.width, to.height / was.rect.height)})`
      : "scale(.8)";
    run(g, [{ transform: "none", opacity: 1 }, { transform: end, opacity: to ? 0.9 : 0, offset: 0.85 },
      { transform: end, opacity: 0 }], duration, () => g.remove());
  }

  // 見えていなかったカードの出どころ: 前の view で束にあればその束、無ければ数が減ったライブラリー・手札
  function origin(id, oldView, newView, before) {
    const was = zoneOf(oldView, id);
    if (was) {
      const key = placeKey(was.key, was.card);
      return key ? before.places.get(key) : null;
    }
    const now = zoneOf(newView, id);
    const owner = now && (now.card.owner || now.card.controller);
    if (!owner) return null;
    for (const z of ["library", "hand"]) {
      const key = `${owner}.${z}`;
      if (oldView.zones[key] && newView.zones[key] && oldView.zones[key].count > newView.zones[key].count) {
        return before.places.get(key);
      }
    }
    return null;
  }

  // 消えたカードの行き先: 新しい view で束・手札にあればそこ、見えなければ数が増えたライブラリー・手札
  function destination(id, oldView, newView, after) {
    const now = zoneOf(newView, id);
    if (now) {
      const key = placeKey(now.key, now.card);
      return key ? after.places.get(key) : null;
    }
    const was = zoneOf(oldView, id);
    const owner = was && (was.card.owner || was.card.controller);
    if (!owner) return null;
    for (const z of ["library", "hand"]) {
      const key = `${owner}.${z}`;
      if (oldView.zones[key] && newView.zones[key] && oldView.zones[key].count < newView.zones[key].count) {
        return after.places.get(key);
      }
    }
    return null;
  }

  // 描画の後に呼ぶ。before は描画の前の capture()
  function play(before, oldView, newView, duration) {
    const after = capture();
    const jobs = [];
    const done = new Set();
    for (const now of after.cards.values()) {
      if (done.has(now)) continue;
      done.add(now);
      const was = now.ids.map((id) => before.cards.get(id)).find(Boolean);
      if (!was) {
        const from = origin(now.ids[0], oldView, newView, before);
        jobs.push(() => (from ? enter(now, from, duration) : appear(now, duration)));
        continue;
      }
      const dx = Math.abs(was.rect.left - now.rect.left), dy = Math.abs(was.rect.top - now.rect.top);
      const dw = Math.abs(was.rect.width - now.rect.width);
      if (dx > 1 || dy > 1 || dw > 1 || was.tapped !== now.tapped) jobs.push(() => move(was, now, duration));
    }
    for (const was of new Set(before.cards.values())) {
      if (was.ids.some((id) => after.cards.has(id))) continue;
      const to = destination(was.ids[0], oldView, newView, after);
      jobs.push(() => leave(was, to, duration));
    }
    if (jobs.length > MAX_JOBS) return;
    for (const job of jobs) job();
  }

  return { capture, stop, play };
}
