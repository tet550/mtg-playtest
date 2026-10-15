// カードの動き（タップ・移動・束への出し入れ）。描画の前後でカードの位置を比べ、写しを重ねて動かす（FLIP）。
// 本物の要素は行や小窓のはみ出しで切られるので、画面全体に重ねた層で写しを動かし、その間は本物を隠す。
// view（公開範囲を適用済み）に無いカードは動かさないので、見えない情報は漏れない。

// 追う対象: 両 Player の盤面・手札・下に差し込んだ追放と、スタックの呪文（戦闘の欄・開いた束の一覧は同じカードの写し）
const TRACKED = "#top [data-ids], #bottom [data-ids], #stack .sitem:not(.ability) [data-ids]";
const MAX_JOBS = 80;  // 一度にこれより多く動くときは（大きな巻き戻しなど）動かさない
const EASE = "cubic-bezier(.2, .7, .2, 1)";
const LISTS = ["cards", "known", "known_positions"];
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
      const item = { el: e, rect, size, pose: pose(e, rect, size, settled), layer: layerOf(e), inStack: !!e.closest("#stack"), tapped: e.classList.contains("tapped"), ids: e.dataset.ids.split(" ") };
      for (const id of item.ids) if (!cards.has(id)) cards.set(id, item);
    }
    const places = new Map();
    for (const e of document.querySelectorAll("[data-zone]")) {
      const rect = (e.querySelector(".pstack") || e).getBoundingClientRect();
      if (rect.width) places.set(e.dataset.zone, rect);
    }
    // スタックの項目（行ごと。能力も）と、スタックの窓
    const rows = new Map();
    for (const li of document.querySelectorAll("#stack > li[data-key]")) {
      const rect = li.getBoundingClientRect();
      // 写しは別の層に置くので、祖先で決まる文字の大きさとカードの大きさ（--h）を取っておく
      const cs = getComputedStyle(li);
      if (rect.width) rows.set(li.dataset.key, { el: li, rect, font: cs.fontSize, h: cs.getPropertyValue("--h") });
    }
    const box = document.getElementById("fl-stack");
    const boxRect = box && box.getBoundingClientRect();
    const stackBox = boxRect && boxRect.width ? { el: box, rect: boxRect } : null;
    return { cards, places, covers: coverRects(), rows, stackBox, clip: clipRects() };
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

  function run(target, frames, duration, done, delay = 0) {
    const a = target.animate(frames, { duration, delay, easing: EASE, fill: "both" });
    animations.push(a);
    a.onfinish = () => { a.cancel(); done && done(); };
    return a;
  }

  // 本物を隠して写しを動かし、終わったら本物に戻す。doom: 戦場から除かれるカードの演出（その間は動き出さない）
  function fly(now, frames, duration, doom) {
    const g = ghost(now.el, now.pose, now.size);
    now.el.style.visibility = "hidden";
    hidden.push(now.el);
    run(g, frames, duration, () => { g.remove(); now.el.style.visibility = ""; }, doom ? doom(g) : 0);
  }

  // 戦場から除かれるカード: 行き先（墓地・追放・手札／ライブラリー）に応じた演出をしてから動かす。
  // 写し（transform で動く）に translate / scale / rotate / filter を重ねるので、動きとは別に当てられる。戻す時間（ms）を返す。
  // lead: 先に見せたい演出（解決の光が対象に当たるまで）の分だけ待つ

  // 破壊・墓地送り: その場で赤く光って震え、砕けた破片が飛び散る
  const SHAKE = 380;
  function shatter(g, at, lead) {
    animations.push(g.animate([
      { translate: "0 0", filter: "none" },
      { translate: "-3px 0", filter: "brightness(1.4) sepia(1) saturate(4) hue-rotate(-30deg)", offset: 0.2 },
      { translate: "3px 1px", offset: 0.35 }, { translate: "-3px -1px", offset: 0.5 }, { translate: "2px 0", offset: 0.65 },
      { translate: "-1px 0", filter: "brightness(1.8) sepia(1) saturate(5) hue-rotate(-30deg)", offset: 0.85 },
      { translate: "0 0", filter: "grayscale(.6) brightness(.8)" }],
    { duration: SHAKE, delay: lead, fill: "both" }));
    for (let i = 0; i < 10; i++) {
      const angle = (i / 10) * Math.PI * 2 + Math.random() * 0.5;
      const dist = 40 + Math.random() * 40;
      burst("shard", at.x + (Math.random() - 0.5) * 30, at.y + (Math.random() - 0.5) * 40, [
        { translate: "0 0", rotate: "0deg", opacity: 1 },
        { translate: `${Math.cos(angle) * dist}px ${Math.sin(angle) * dist + 20}px`, rotate: `${(Math.random() - 0.5) * 540}deg`, opacity: 0 }],
      { duration: 520, delay: lead + SHAKE * 0.85, easing: "cubic-bezier(.2, .8, .4, 1)" });
    }
    burst("shatterring", at.x, at.y, [{ scale: "0.3", opacity: 0.9 }, { scale: "1.4", opacity: 0 }],
      { duration: 420, delay: lead + SHAKE * 0.85, easing: "ease-out" });
    return lead + SHAKE;
  }

  // バウンス（手札・ライブラリーへ戻す）: 風の輪とともにふわっと浮き上がってから戻る
  const LIFT = 280;
  function bounce(g, at, lead) {
    animations.push(g.animate([
      { translate: "0 0", scale: "1", rotate: "0deg", filter: "none" },
      { translate: "0 -12px", scale: "1.07", rotate: "-3deg",
        filter: "brightness(1.25) drop-shadow(0 8px 14px rgba(80, 195, 255, .8))", offset: 0.55 },
      { translate: "0 -4px", scale: "1.02", rotate: "0deg",
        filter: "brightness(1.1) drop-shadow(0 4px 8px rgba(80, 195, 255, .4))", offset: 0.85 },
      { translate: "0 0", scale: "1", rotate: "0deg", filter: "none" }],
    { duration: LIFT, delay: lead, easing: "cubic-bezier(.2, .8, .3, 1)", fill: "both" }));
    for (let i = 0; i < 2; i++) {
      burst("bouncering", at.x, at.y + 8, [
        { translate: "0 6px", scale: "0.35 0.2", opacity: 0.9 },
        { translate: "0 -18px", scale: "1.35 0.75", opacity: 0 }],
      { duration: 380, delay: lead + i * 90, easing: "ease-out" });
    }
    for (let i = 0; i < 6; i++) {
      const dx = (i - 2.5) * 14 + (Math.random() - 0.5) * 8;
      burst("gust", at.x + dx, at.y + (Math.random() - 0.5) * 20, [
        { translate: "0 12px", scale: "0.7 1", opacity: 0 },
        { translate: "0 -4px", scale: "1 1.1", opacity: 0.9, offset: 0.3 },
        { translate: `${dx * 0.3}px ${-32 - Math.random() * 20}px`, scale: "0.5 0.6", opacity: 0 }],
      { duration: 360, delay: lead + 30 + i * 25, easing: "cubic-bezier(.2, .8, .3, 1)" });
    }
    return lead + LIFT;
  }

  // 追放・一時追放: 白紫の光に包まれ、収束する輪と光の粒とともに別次元へ吸い込まれる
  const BANISH = 360;
  function banish(g, at, lead) {
    animations.push(g.animate([
      { scale: "1", filter: "none" },
      { scale: "1.05", filter: "brightness(1.6) saturate(.2) contrast(1.2) drop-shadow(0 0 14px rgba(195, 155, 255, .95))", offset: 0.35 },
      { scale: "0.88", filter: "brightness(2) grayscale(.8) sepia(.4) hue-rotate(220deg) saturate(3) drop-shadow(0 0 18px rgba(180, 130, 255, 1))", offset: 0.75 },
      { scale: "1", filter: "grayscale(.4) brightness(.85)" }],
    { duration: BANISH, delay: lead, easing: "ease-in-out", fill: "both" }));
    burst("banishring", at.x, at.y, [
      { scale: "1.35", rotate: "0deg", opacity: 0 },
      { scale: "1.1", opacity: 0.95, offset: 0.25 },
      { scale: "0.15", rotate: "120deg", opacity: 0 }],
    { duration: 420, delay: lead + 40, easing: "cubic-bezier(.4, 0, .2, 1)" });
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2 + Math.random() * 0.4;
      const dist = 38 + Math.random() * 22;
      const x0 = Math.cos(angle) * dist, y0 = Math.sin(angle) * dist;
      burst("banishmote", at.x, at.y, [
        { translate: `${x0}px ${y0}px`, scale: "0.5", opacity: 0 },
        { translate: `${x0 * 0.65}px ${y0 * 0.65}px`, scale: "1.1", opacity: 1, offset: 0.35 },
        { translate: `${x0 * 0.1}px ${y0 * 0.1 - 10}px`, scale: "0.2", opacity: 0 }],
      { duration: 400, delay: lead + 20 + i * 20, easing: "cubic-bezier(.3, 0, .2, 1)" });
    }
    return lead + BANISH;
  }

  // 前の位置・向きから今の位置へ（タップは 90° 回す）。hide: 始め・終わりが窓の下に隠れているか（隠れている側は透明にする）
  // 始めと終わりの transform は同じ並び（translate rotate scale matrix）にして、1つずつ補間させる
  const pos = (x, y, turn, sx, sy, tf) => `translate(${x}px, ${y}px) rotate(${turn}deg) scale(${sx}, ${sy}) ${tf}`;
  const STILL = "matrix(1, 0, 0, 1, 0, 0)";

  function move(was, now, duration, hide, doom) {
    const a = was.pose, b = now.pose;
    const turn = was.tapped === now.tapped ? 0 : now.tapped ? -90 : 90;
    const [sx, sy] = turn
      ? [was.size.h / now.size.w, was.size.w / now.size.h]
      : [was.size.w / now.size.w, was.size.h / now.size.h];
    fly(now, [{ transform: pos(a.x - b.x, a.y - b.y, turn, sx, sy, a.tf), opacity: hide[0] ? 0 : 1 },
      { transform: pos(0, 0, 0, 1, 1, b.tf), opacity: hide[1] ? 0 : 1 }], duration, doom);
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
  function leave(was, to, duration, hide, doom) {
    const g = ghost(was.el, was.pose, was.size);  // was.el は描き直しで画面から外れている
    const a = was.pose;
    const s = to ? Math.min(to.width / was.size.w, to.height / was.size.h) : 0.8;
    const end = to ? pos(center(to).x - a.x, center(to).y - a.y, 0, s, s, STILL) : pos(0, 0, 0, s, s, a.tf);
    run(g, [{ transform: pos(0, 0, 0, 1, 1, a.tf), opacity: hide[0] ? 0 : 1 }, { transform: end, opacity: hide[1] ? 0 : 0.9, offset: 0.85 },
      { transform: end, opacity: 0 }], duration, () => g.remove(), doom ? doom(g) : 0);
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
  function clipRects() {
    const rects = [];
    const add = (l, t, r, b) => { if (r > l && b > t) rects.push([l, t, r, b]); };
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
    return rects;
  }

  // 描く前と後の見える所を合わせる（スタックの最後の項目が解決すると窓が消えるが、写しはその窓から動き出す）
  function clipTo(rects) {
    const d = rects.map(([l, t, r, b]) => `M${l} ${t}H${r}V${b}H${l}Z`).join("");
    layer.style.clipPath = d ? `path("${d}")` : "";
  }

  // スタックの項目（後から積んだものが先に解決する）: 積まれた項目は一番上へ上から差し込まれ、下の項目は1段ずつ押し下げられる。
  // 解決した項目は一番上から光って浮き上がって抜け、残りが1段ずつ繰り上がる。forward でなければ（戻す）、抜ける項目は消えるだけ
  // HOLD: 解決した項目が光っている間（呪文のカードはスタックに留まり、それから行き先へ飛ぶ）
  const POP = 520, HOLD = 210, PUSH = 120;
  function stackRows(before, after, forward, duration) {
    const gone = [...before.rows].filter(([key]) => !after.rows.has(key)).map(([, was]) => was);
    // 最後の項目が抜けて窓が消えるときは、空になった窓の写しを残して消していく
    if (before.stackBox && !after.stackBox && gone.length) {
      const g = before.stackBox.el.cloneNode(true);
      g.hidden = false;
      g.removeAttribute("id");
      g.querySelector("ol").replaceChildren();
      const r = before.stackBox.rect;
      Object.assign(g.style, { position: "fixed", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
        right: "auto", bottom: "auto", margin: "0", transform: "none", maxHeight: "none", boxSizing: "border-box" });
      layer.append(g);
      run(g, [{ opacity: 1 }, { opacity: 1, offset: 0.6 }, { opacity: 0 }], POP + duration * 0.5, () => g.remove());
    }
    if (!before.stackBox && after.stackBox) run(after.stackBox.el, [{ opacity: 0, scale: "0.96" }, { opacity: 1, scale: "1" }], 220);
    for (const was of gone) {
      const g = was.el.cloneNode(true);
      // 呪文のカードは、カードの動きで行き先へ飛ぶので、行の写しからは外す
      if (!was.el.classList.contains("ability")) for (const c of g.querySelectorAll("[data-ids]")) c.style.visibility = "hidden";
      const resolved = forward && !was.el.classList.contains("pv");
      g.classList.add("rowghost");
      g.classList.toggle("resolving", resolved);
      const r = was.rect;
      Object.assign(g.style, { position: "fixed", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
        margin: "0", boxSizing: "border-box", fontSize: was.font });
      if (was.h) g.style.setProperty("--h", was.h);
      layer.append(g);
      // 光ってから抜けるのが見えるよう、動き全体の緩急（EASE: 始めが速い）は使わない
      const a = g.animate(resolved ? [
        { translate: "0 0", scale: "1", opacity: 1, filter: "none", easing: "ease-out" },
        { translate: "0 0", scale: "1", opacity: 1, filter: "brightness(1.2)", offset: HOLD / POP, easing: "ease-in" },
        { translate: "0 -40px", scale: "0.96", opacity: 0, filter: "brightness(1.4)" }]
        : [{ opacity: 1 }, { opacity: 0 }], { duration: resolved ? POP : duration * 0.6, fill: "both" });
      animations.push(a);
      a.onfinish = () => { a.cancel(); g.remove(); };
    }
    let pushed = false;
    for (const [key, now] of after.rows) {
      const was = before.rows.get(key);
      if (!was) continue;
      const dx = was.rect.left - now.rect.left, dy = was.rect.top - now.rect.top;
      if (Math.abs(dx) <= 1 && Math.abs(dy) <= 1) continue;
      pushed = pushed || dy < 0;
      // 繰り上がるのは、上の項目が光り終えて抜け始めてから
      run(now.el, [{ translate: `${dx}px ${dy}px` }, { translate: "0 0" }], duration, null, gone.length && dy > 0 ? HOLD : 0);
    }
    for (const [key, now] of after.rows) {
      if (before.rows.has(key)) continue;
      // 差し込むのは、下の項目が退きかけてから
      const delay = pushed ? PUSH : 0;
      run(now.el, [
        { translate: "0 -28px", scale: "1.05", opacity: 0 },
        { translate: "0 3px", scale: "1", opacity: 1, offset: 0.7 },
        { translate: "0 0", scale: "1", opacity: 1 }], duration, null, delay);
      const r = now.rect;
      const f = burst("stackpush", r.left, r.top, [{ opacity: 0 }, { opacity: 1, offset: 0.25 }, { opacity: 0 }],
        { duration: duration + 240, delay: delay + duration * 0.5, easing: "ease-out" });
      Object.assign(f.effect.target.style, { width: `${r.width}px`, height: `${r.height}px` });
    }
  }

  // 描画の後に呼ぶ。before は描画の前の capture()。lead: 除去の演出を始めるまで待つ時間（解決の光が当たるまで）
  function play(before, oldView, newView, duration, lead = 0) {
    const after = capture(true);
    clipTo(before.clip.concat(after.clip));
    const jobs = [];
    const done = new Set();
    // 始め・終わりが窓の下に隠れているか。両方とも隠れていれば動かさない（写しが窓の上に出てしまうので）
    const hidden0 = (rect, layer) => covered(rect, layer, before.covers);
    const hidden1 = (rect, layer) => covered(rect, layer, after.covers);
    const add = (hide, job) => { if (!(hide[0] && hide[1])) jobs.push(() => job(hide)); };
    // 戦場から除かれた（戦場にあって、今は戦場に無い）カードは、行き先に応じた演出をしてから動く。進めたときだけ。
    // 下書き・送った依頼で仮に戦場に置いたカード（planned）は、仮の盤面が消えただけなので除かない
    const forward = newView.version > oldView.version;
    const onField = (view, id) => (zoneOf(view, id) || {}).key === "battlefield";
    const real = (id) => !zoneOf(oldView, id).card.planned;
    const removalOf = (id) => {
      const now = zoneOf(newView, id);
      if (now) {
        if (now.key === "exile") return banish;
        if (/\.(hand|library)$/.test(now.key)) return bounce;
        return shatter;
      }
      const was = zoneOf(oldView, id);
      const owner = was && (was.card.owner || was.card.controller);
      if (owner && ["library", "hand"].some((z) => {
        const key = `${owner}.${z}`;
        return oldView.zones[key] && newView.zones[key] && oldView.zones[key].count < newView.zones[key].count;
      })) return bounce;
      return shatter;
    };
    const doomOf = (item, hide) => {
      if (!forward || hide[0]) return null;
      // スタックから出ていく呪文のカードは、項目が光り終わるまで留まる
      if (item.inStack && !item.ids.some((i) => (zoneOf(newView, i) || {}).key === "stack")) return () => HOLD;
      const id = item.ids.find((i) => onField(oldView, i) && real(i) && !onField(newView, i));
      if (!id) return null;
      const fx = removalOf(id);
      return (g) => fx(g, center(item.rect), lead);
    };
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
        add([hidden0(was.rect, was.layer), hidden1(now.rect, now.layer)], (hide) => move(was, now, duration, hide, doomOf(was, hide)));
      }
    }
    for (const was of new Set(before.cards.values())) {
      if (was.ids.some((id) => after.cards.has(id))) continue;
      const to = destination(was.ids[0], oldView, newView, after);
      add([hidden0(was.rect, was.layer), !to || hidden1(to, -1)], (hide) => leave(was, to, duration, hide, doomOf(was, hide)));
    }
    stackRows(before, after, forward, duration);  // カードの写しより下の段に
    if (jobs.length > MAX_JOBS) return;
    for (const job of jobs) job();
  }

  // 戦闘ダメージに入ったとき、戦闘の欄で攻撃クリーチャーとブロックしているカードがぶつかり合う。
  // ブロックされていなければ、攻撃先の向き（矢印）へ突っ込む。続けて描き直しても止めない（stop() とは別に持つ）
  // (x, y) に置いた飾りの要素を動かし、終わったら消す（カードの写しの層とは別に、画面の一番上に置く）
  function burst(cls, x, y, frames, timing) {
    const s = document.createElement("div");
    s.className = "fx " + cls;
    Object.assign(s.style, { left: `${x}px`, top: `${y}px` });
    document.body.append(s);
    const a = s.animate(frames, { fill: "both", ...timing });
    a.onfinish = a.oncancel = () => s.remove();
    return a;
  }

  let clashes = [];
  function clash() {
    for (const a of clashes) a.cancel();
    clashes = [];
    for (const e of document.querySelectorAll(".clashspark")) e.remove();
    const box = document.getElementById("combatbox");
    if (!box || box.hidden || box.classList.contains("min")) return;
    const D = 680, HIT = 0.42;
    const has = (e, id) => e.dataset.ids.split(" ").includes(id);
    const hit = (e, dx, dy) => {
      const a = e.animate([
        { translate: "0 0", filter: "none", easing: "ease-out" },
        { translate: `${-dx * 0.18}px ${-dy * 0.18}px`, offset: 0.24, easing: "cubic-bezier(.6, 0, 1, .6)" },  // 振りかぶる
        { translate: `${dx}px ${dy}px`, filter: "brightness(1.8)", offset: HIT },
        { translate: `${dx * 0.7 + 4}px ${dy * 0.7}px`, filter: "brightness(1.2)", offset: HIT + 0.08 },        // はね返って揺れる
        { translate: `${dx * 0.6 - 4}px ${dy * 0.6}px`, offset: HIT + 0.16 },
        { translate: `${dx * 0.5 + 2}px ${dy * 0.5}px`, filter: "none", offset: HIT + 0.24, easing: EASE },
        { translate: "0 0", filter: "none" },
      ], D);
      clashes.push(a);
    };
    const spark = (x, y) => clashes.push(burst("clashspark", x, y,
      [{ scale: "0.2", opacity: 1 }, { scale: "1.3", opacity: 1, offset: 0.4 }, { scale: "1.7", opacity: 0 }],
      { duration: D * 0.5, delay: D * HIT, easing: "ease-out" }));
    for (const col of box.querySelectorAll(".cbcol")) {
      const attacker = [...col.children].find((e) => e.dataset.ids && has(e, col.dataset.key));
      if (!attacker) continue;
      const a = attacker.getBoundingClientRect();
      const blockers = [...col.querySelectorAll(".cbrow > [data-ids]:not(.plannedblk)")];
      if (!blockers.length) {
        // 攻撃先へ突っ込む（↑ ↓ は上下の Player、→ はそれ以外）
        const arrow = (col.querySelector(".cbto .arw") || {}).textContent;
        const [ux, uy] = arrow === "↑" ? [0, -1] : arrow === "↓" ? [0, 1] : [1, 0];
        const reach = Math.min(40, a.height * 0.35);
        hit(attacker, ux * reach, uy * reach);
        spark(a.left + a.width / 2 + ux * (a.width / 2 + reach), a.top + a.height / 2 + uy * (a.height / 2 + reach));
        continue;
      }
      const rects = blockers.map((b) => b.getBoundingClientRect());
      const below = rects[0].top > a.top;  // ブロックが攻撃カードの下（下の Player を攻撃している）
      const edge = below ? Math.min(...rects.map((r) => r.top)) : Math.max(...rects.map((r) => r.bottom));
      const front = below ? a.bottom : a.top;
      const meet = (front + edge) / 2;
      const ax = a.left + a.width / 2;
      hit(attacker, 0, meet - front + (below ? 4 : -4));
      blockers.forEach((b, i) => {
        const r = rects[i];
        hit(b, (ax - (r.left + r.width / 2)) * 0.4, meet - edge + (below ? -4 : 4));
      });
      spark(ax, meet);
    }
  }

  // 呪文・能力の解決: スタックの項目の所で光の輪が広がり、対象（カード・Player）へ光の玉が飛んで当たる。
  // items: [{ from: 解決する前の項目の位置, targets: [対象の位置] }]。ルールの判断はしない（打ち消されても同じ）
  let resolves = [];
  function resolve(items) {
    for (const a of resolves) a.cancel();
    resolves = [];
    const D = 760, FLY = 0.2, HIT = 0.55;
    for (const { from, targets, flash } of items) {
      const a = center(from);
      resolves.push(burst("resolvering", a.x, a.y, [{ scale: "0.3", opacity: 0.9 }, { scale: "1.5", opacity: 0 }],
        { duration: D * 0.5, easing: "cubic-bezier(.1, .7, .3, 1)" }));
      for (const to of targets) {
        const b = center(to);
        resolves.push(burst("resolveorb", a.x, a.y, [
          { translate: "0 0", scale: "0.4", opacity: 0 },
          { translate: "0 0", scale: "1", opacity: 1, offset: 0.25 },
          { translate: `${b.x - a.x}px ${b.y - a.y}px`, scale: "0.8", opacity: 1 }],
        { duration: D * (HIT - FLY), delay: D * FLY, easing: "cubic-bezier(.5, 0, .8, .6)" }));
        resolves.push(burst("resolvering hit", b.x, b.y, [{ scale: "0.2", opacity: 1 }, { scale: "1.2", opacity: 0 }],
          { duration: D * (1 - HIT), delay: D * HIT, easing: "ease-out" }));
      }
      for (const e of flash) {
        resolves.push(e.animate([{ filter: "none" }, { filter: "brightness(1.9) drop-shadow(0 0 10px #ffd84a)", offset: 0.3 }, { filter: "none" }],
          { duration: D * (1 - HIT), delay: D * HIT }));
      }
    }
  }

  return { capture, stop, play, clash, resolve };
}
