// カードの動き（タップ・移動・束への出し入れ）。描画の前後でカードの位置を比べ、写しを重ねて動かす（FLIP）。
// 本物の要素は行や小窓のはみ出しで切られるので、画面全体に重ねた層で写しを動かし、その間は本物を隠す。
// view（公開範囲を適用済み）に無いカードは動かさないので、見えない情報は漏れない。

// 追う対象: 両 Player の盤面・手札・下に差し込んだ追放と、スタックの呪文（戦闘の欄・開いた束の一覧は同じカードの写し）
const TRACKED = "#top [data-ids], #bottom [data-ids], #stack .sitem:not(.ability) [data-ids]";
const MAX_JOBS = 80;  // 一度にこれより多く動くときは（大きな巻き戻しなど）動かさない
const EASE = "cubic-bezier(.2, .7, .2, 1)";
const LISTS = ["cards", "known", "known_positions", "known_unordered"];
// 盤面の上に重なる窓（下から順）。写しは窓より上の層で動くので、窓の下に隠れたカードの写しは出さない（窓の上に飛び出さない）
const COVERS = [".zone.opened", "#combatbox", "#fl-stack", "#fl-detail", "#fl-play", "body.log-open .logpane"];

function center(r) {
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

// 要素が入っている窓の段（COVERS の番号。盤面そのものなら -1）
function layerOf(e) {
  let layer = -1;
  COVERS.forEach((sel, i) => { if (e.closest(sel)) layer = i; });
  return layer;
}

// 今見えている窓と、その位置
function coverRects() {
  const covers = [];
  COVERS.forEach((sel, i) => {
    for (const e of document.querySelectorAll(sel)) {
      const rect = e.getBoundingClientRect();
      if (rect.width && rect.height) covers.push({ layer: i, rect });
    }
  });
  return covers;
}

// rect の中心が、layer より上の窓の下に隠れているか
function covered(rect, layer, covers) {
  const p = center(rect);
  return covers.some((c) => c.layer > layer && p.x >= c.rect.left && p.x <= c.rect.right && p.y >= c.rect.top && p.y <= c.rect.bottom);
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

  // 今の画面のカードと束の位置。settled: 傾きが変わりかけなら行き着く先で測る（描画の後。前は今見えている傾き）
  function capture(settled = false) {
    const cards = new Map();
    for (const e of document.querySelectorAll(TRACKED)) {
      if (e.closest(".zone.opened")) continue;
      const rect = e.getBoundingClientRect();
      if (!rect.width) continue;
      const size = measure(e);
      const item = { el: e, rect, size, pose: pose(e, rect, size, settled), layer: layerOf(e), tapped: e.classList.contains("tapped"), ids: e.dataset.ids.split(" ") };
      for (const id of item.ids) if (!cards.has(id)) cards.set(id, item);
    }
    const places = new Map();
    for (const e of document.querySelectorAll("[data-zone]")) {
      const rect = (e.querySelector(".pstack") || e).getBoundingClientRect();
      if (rect.width) places.set(e.dataset.zone, rect);
    }
    return { cards, places, covers: coverRects() };
  }

  // 動いている途中なら、すぐ終わらせる（次の描画の前に呼ぶ）
  function stop() {
    for (const a of animations) a.cancel();
    animations = [];
    for (const e of hidden) e.style.visibility = "";
    hidden = [];
    layer.replaceChildren();
  }

  // 祖先のクラスで決まる大きさ（写しは別の層に置くので、測った値で固定する）。画面から外れた要素は測れないので、
  // 描く前の capture() で測っておく
  function measure(e) {
    const box = e.querySelector(".artbox");
    const img = box && box.querySelector("img.art");
    return { w: e.offsetWidth, h: e.offsetHeight, box: box && [box.offsetWidth, box.offsetHeight], img: img && [img.offsetWidth, img.offsetHeight] };
  }

  // 要素の置き場所（transform を除いた箱の中心）と、要素自身の transform（手札の扇の傾き・ホバーで浮かせた分）。
  // 写しは傾きごと動かす（傾きを外して動かすと、手札の枚数が変わったときに全部のカードが真っすぐになってから傾き直す）。
  // settled なら、傾きが変わりかけ（CSS の transition の途中）でも行き着く先の傾きで測る
  function pose(e, rect, size, settled) {
    const cs = getComputedStyle(e);
    let m = new DOMMatrixReadOnly(cs.transform === "none" ? undefined : cs.transform);
    const t = settled && e.getAnimations().find((a) => a.transitionProperty === "transform");
    if (t) {
      const frames = t.effect.getKeyframes();
      try { m = new DOMMatrixReadOnly(frames[frames.length - 1].transform); } catch { /* 読めなければ今の傾き */ }
    }
    const [ox, oy] = cs.transformOrigin.split(" ").map(parseFloat);
    const dx = ox - size.w / 2, dy = oy - size.h / 2;  // 箱の中心から transform の原点まで
    // 見えている外接の箱の中心 = 原点 + m（中心 − 原点）から、箱の中心を逆に求める（今の傾きで測った rect から）
    const now = new DOMMatrixReadOnly(cs.transform === "none" ? undefined : cs.transform);
    const c = center(rect);
    return { x: c.x - dx + now.a * dx + now.c * dy - now.e, y: c.y - dy + now.b * dx + now.d * dy - now.f,
      tf: m.toString(), origin: cs.transformOrigin };
  }

  // 要素の写しを、その位置に重ねる（transform は動きの keyframe で与える）
  function ghost(e, pose, size = measure(e)) {
    const g = e.cloneNode(true);
    g.classList.remove("sel");
    g.style.visibility = "";
    Object.assign(g.style, { position: "fixed", left: `${pose.x - size.w / 2}px`, top: `${pose.y - size.h / 2}px`, margin: "0",
      width: `${size.w}px`, height: `${size.h}px`, boxSizing: "border-box", transformOrigin: pose.origin, transition: "none" });
    const gbox = g.querySelector(".artbox");
    if (size.box && gbox) {
      Object.assign(gbox.style, { width: `${size.box[0]}px`, height: `${size.box[1]}px` });
      const gimg = gbox.querySelector("img.art");
      if (size.img && gimg) Object.assign(gimg.style, { width: `${size.img[0]}px`, height: `${size.img[1]}px` });
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
    const g = ghost(now.el, now.pose, now.size);
    now.el.style.visibility = "hidden";
    hidden.push(now.el);
    run(g, frames, duration, () => { g.remove(); now.el.style.visibility = ""; });
  }

  // 前の位置・向きから今の位置へ（タップは 90° 回す）。hide: 始め・終わりが窓の下に隠れているか（隠れている側は透明にする）
  // 始めと終わりの transform は同じ並び（translate rotate scale matrix）にして、1つずつ補間させる
  const pos = (x, y, turn, sx, sy, tf) => `translate(${x}px, ${y}px) rotate(${turn}deg) scale(${sx}, ${sy}) ${tf}`;
  const STILL = "matrix(1, 0, 0, 1, 0, 0)";

  function move(was, now, duration, hide) {
    const a = was.pose, b = now.pose;
    const turn = was.tapped === now.tapped ? 0 : now.tapped ? -90 : 90;
    const [sx, sy] = turn
      ? [was.size.h / now.size.w, was.size.w / now.size.h]
      : [was.size.w / now.size.w, was.size.h / now.size.h];
    fly(now, [{ transform: pos(a.x - b.x, a.y - b.y, turn, sx, sy, a.tf), opacity: hide[0] ? 0 : 1 },
      { transform: pos(0, 0, 0, 1, 1, b.tf), opacity: hide[1] ? 0 : 1 }], duration);
  }

  // 束（ライブラリーなど）から出てくる
  function enter(now, from, duration, hide) {
    const a = center(from), b = now.pose;
    const s = Math.min(from.width / now.size.w, from.height / now.size.h);
    fly(now, [{ transform: pos(a.x - b.x, a.y - b.y, 0, s, s, STILL), opacity: hide[0] ? 0 : 0.4 },
      { transform: pos(0, 0, 0, 1, 1, b.tf), opacity: hide[1] ? 0 : 1 }], duration);
  }

  // どこからか分からないもの（トークンなど）は、その場に現れる
  function appear(now, duration) {
    run(now.el, [{ scale: ".6", opacity: 0 }, { scale: "1", opacity: 1 }], duration);  // transform（扇の傾き）とは別に
  }

  // 画面から消えるカードは、写しを行き先の束まで飛ばす（行き先が見えなければ、その場で消える）
  function leave(was, to, duration, hide) {
    const g = ghost(was.el, was.pose, was.size);  // was.el は描き直しで画面から外れている
    const a = was.pose;
    const s = to ? Math.min(to.width / was.size.w, to.height / was.size.h) : 0.8;
    const end = to ? pos(center(to).x - a.x, center(to).y - a.y, 0, s, s, STILL) : pos(0, 0, 0, s, s, a.tf);
    run(g, [{ transform: pos(0, 0, 0, 1, 1, a.tf), opacity: hide[0] ? 0 : 1 }, { transform: end, opacity: hide[1] ? 0 : 0.9, offset: 0.85 },
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

  // 写しを見せるのは、本物のカードが見える所（各 Player の欄の枠の内側のうち見出し（名前・ライフ）より下と、
  // 盤面の上の小窓）だけ。手札のように欄の端で切れているカードの写しも、枠・見出し・その上の余白には出ない
  function clipToSides() {
    let d = "";
    const add = (l, t, r, b) => { if (r > l && b > t) d += `M${l} ${t}H${r}V${b}H${l}Z`; };
    for (const side of document.querySelectorAll("#top, #bottom")) {
      const r = side.getBoundingClientRect();
      const l = r.left + side.clientLeft, t = r.top + side.clientTop;
      const head = side.querySelector(".phead");
      const below = head ? Math.max(t, head.getBoundingClientRect().bottom) : t;
      add(l, below, l + side.clientWidth, t + side.clientHeight);
    }
    for (const e of document.querySelectorAll("#combatbox, #fl-stack, #fl-detail")) {
      const r = e.getBoundingClientRect();
      if (r.width && r.height) add(r.left, r.top, r.right, r.bottom);
    }
    layer.style.clipPath = d ? `path("${d}")` : "";
  }

  // 描画の後に呼ぶ。before は描画の前の capture()
  function play(before, oldView, newView, duration) {
    clipToSides();
    const after = capture(true);
    const jobs = [];
    const done = new Set();
    // 始め・終わりが窓の下に隠れているか。両方とも隠れていれば動かさない（写しが窓の上に出てしまうので）
    const hidden0 = (rect, layer) => covered(rect, layer, before.covers);
    const hidden1 = (rect, layer) => covered(rect, layer, after.covers);
    const add = (hide, job) => { if (!(hide[0] && hide[1])) jobs.push(() => job(hide)); };
    for (const now of after.cards.values()) {
      if (done.has(now)) continue;
      done.add(now);
      const was = now.ids.map((id) => before.cards.get(id)).find(Boolean);
      if (!was) {
        const from = origin(now.ids[0], oldView, newView, before);
        if (from) add([hidden0(from, -1), hidden1(now.rect, now.layer)], (hide) => enter(now, from, duration, hide));
        else jobs.push(() => appear(now, duration));
        continue;
      }
      const dx = Math.abs(was.pose.x - now.pose.x), dy = Math.abs(was.pose.y - now.pose.y);
      const dw = Math.abs(was.size.w - now.size.w);
      if (dx > 1 || dy > 1 || dw > 1 || was.tapped !== now.tapped || was.pose.tf !== now.pose.tf) {
        add([hidden0(was.rect, was.layer), hidden1(now.rect, now.layer)], (hide) => move(was, now, duration, hide));
      }
    }
    for (const was of new Set(before.cards.values())) {
      if (was.ids.some((id) => after.cards.has(id))) continue;
      const to = destination(was.ids[0], oldView, newView, after);
      add([hidden0(was.rect, was.layer), !to || hidden1(to, -1)], (hide) => leave(was, to, duration, hide));
    }
    if (jobs.length > MAX_JOBS) return;
    for (const job of jobs) job();
  }

  return { capture, stop, play };
}
