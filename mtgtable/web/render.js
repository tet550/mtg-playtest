import { $, dots, el, noSelect } from "./dom.js";
import { patch } from "./patch.js";
import { createMotion } from "./motion.js";
import { createStack } from "./stack.js";
import { t as tr } from "./i18n.js";  // t はターン（v.turn）などの名前に使っているので tr
import { createCardNames, displayName, localize } from "./cardnames.js";

// DOM 描画。通信と再生操作は呼び出し元から渡す。
// hooks（GUI の対局）: card / pile / player / stack はクリックを受け取り、true を返したら既定の動き（詳細・開閉）をしない。
// after は描画の後に呼ぶ。ui.marks（id → class）で、選んだ対象・支払いなどをカードに色で出す
export function createRenderer(ui, { source, viewAt, onLogSeek, clampFloats, hooks = {} }) {
  const { imageURL, symbolURL } = source;
  const motion = createMotion();
  const stack = createStack($("fl-stack"), $("stack"));
  // カード名の日本語（日本語の画面で、見えているカードの名前だけ聞く）。分かったら描き直す
  const cardNames = createCardNames(source, () => { render(); renderLog(); });
  // 読めなかった画像の URL。描き直しは差分で当てるので、次からは組み立ての時点で文字にする（読み直しを繰り返さない）
  const broken = new Set();
  const failed = (url) => { broken.add(url); return true; };
  // 文の中の {G} {2} {T} {W/U} などをマナ・シンボルの画像にした要素の並び（画像を使わないときは文字のまま）
  function manaNodes(text) {
    if (!ui.images) return [document.createTextNode(text)];
    const out = [];
    let last = 0;
    for (const m of text.matchAll(/\{([^{}]{1,5})\}/g)) {
      if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
      const url = symbolURL(m[1].replace("/", ""));
      if (broken.has(url)) out.push(document.createTextNode(m[0]));
      else {
        const img = el("img", "ms");
        img.alt = m[0];
        img.title = m[0];
        img.src = url;
        img.onerror = (e) => failed(url) && e.currentTarget.replaceWith(document.createTextNode(m[0]));
        out.push(img);
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push(document.createTextNode(text.slice(last)));
    return out;
  }
  const manaEl = (tag, cls, text) => { const e = el(tag, cls); e.append(...manaNodes(text)); return e; };
  // ---------------------------------------------------------------- 描画

  function collectNames(v) {
    const names = {};
    for (const z of Object.values(v.zones)) {
      for (const key of ["cards", "known", "known_positions"]) {
        for (const c of z[key] || []) if (c.name) names[c.id] = c.name;
      }
    }
    return names;
  }

  const ref = (id) => (ui.names[id] ? `<${displayName(ui.names[id])}> ${id}` : id);

  // 同じ見た目のパーマネント（Pizza のコピー ×100 など）は1枚にまとめて ×N で出す。見た目に出ない id（Note の id）は
  // 比べない。並びが離れていても（間に別のカードが出ても）まとめ、最初に出た位置に置く
  function groupCards(cards) {
    const groups = [];
    const byKey = new Map();
    for (const c of cards) {
      const { id, notes, ...rest } = c;
      const key = JSON.stringify({ ...rest, notes: (notes || []).map((n) => [n.text, n.until || null]) });
      const g = byKey.get(key);
      if (g) g.ids.push(id);
      else { const ng = { key, card: c, ids: [id] }; byKey.set(key, ng); groups.push(ng); }
    }
    return groups.map((g) => (g.ids.length === 1 ? g.card
      : { ...g.card, id: `${g.ids[0]}..${g.ids[g.ids.length - 1]}`, count: g.ids.length, ids: g.ids }));
  }

  // 画像の上に、エンジンが持っている情報（カウンター・×N）を重ねる。画像が無ければ名前の文字だけ
  function art(c, pt) {
    const box = el("div", "artbox");
    if (c.face_down || !c.name) {
      box.classList.add("back");
    } else if (broken.has(imageURL(c.name))) {
      box.classList.add("noimg");
    } else {
      const img = el("img", "art");
      img.loading = "lazy";
      img.alt = displayName(c.name);
      const url = img.src = imageURL(c.name);
      img.onerror = (e) => { failed(url); const t = e.currentTarget; t.parentElement && t.parentElement.classList.add("noimg"); t.remove(); };
      box.append(img);
    }
    const flags = el("div", "flags");
    if (c.token) flags.append(el("span", "chip strong", "token"));
    if (c.new) flags.append(el("span", "chip strong", "new"));
    if (ui.attacking.has(c.id)) flags.append(el("span", "chip strong atk", tr("card.attacking")));
    if (ui.blocking.has(c.id)) flags.append(el("span", "chip strong blk", tr("card.blocking")));
    if (c.controller && c.owner && c.controller !== c.owner) flags.append(el("span", "chip strong", "owner " + c.owner));
    const texts = cardTexts(c);
    if (texts.length) {
      const notes = el("div", "notes");
      for (const t of texts) {
        const n = manaEl("div", "nt", t);
        n.title = t;
        notes.append(n);
      }
      box.append(notes);
    }
    const over = el("div", "over");
    if (c.pos) over.append(el("span", "chip strong", c.pos));
    if (c.count) over.append(el("span", "chip strong", `×${c.count}`));
    for (const [k, n] of Object.entries(c.counters || {})) over.append(el("span", "chip strong", `${k} ×${n}`));
    box.append(flags, over);
    if (pt) box.append(pt);
    return box;
  }

  // カードが負っているダメージ（「damage N」の Note の合計）
  const damageOf = (c) => (c.notes || []).reduce((n, note) => {
    const d = /^damage (\d+)/i.exec(note.text);
    return d ? n + Number(d[1]) : n;
  }, 0);

  // 戦闘中の P/T の目安: 印刷された（トークンは定義の）P/T に、±N/±N のカウンターと Note（「+2/+2」で始まるもの）を足す。
  // オーラ・常在型能力などは数えない（ルールの判断は審判）。「damage N」の Note はダメージとして横に出す
  function ptOf(c) {
    if (!c.base_pt) return null;
    const base = c.base_pt.map((x) => (/^-?\d+$/.test(x) ? Number(x) : null));
    let [p, t] = base;
    const mods = [];
    const add = (m, n, what) => {
      if (p !== null) p += Number(m[1]) * n;
      if (t !== null) t += Number(m[2]) * n;
      mods.push(n === 1 ? what : `${what} ×${n}`);
    };
    for (const [k, n] of Object.entries(c.counters || {})) {
      const m = /^([+-]\d+)\/([+-]\d+)$/.exec(k);
      if (m) add(m, n, tr("card.counter", { kind: k }));
    }
    for (const note of c.notes || []) {
      const m = /^([+-]\d+)\/([+-]\d+)/.exec(note.text);
      if (m) add(m, 1, note.text);
    }
    const damage = damageOf(c);
    const text = `${p ?? c.base_pt[0]}/${t ?? c.base_pt[1]}`;
    const known = p !== null && t !== null && base[0] !== null && base[1] !== null;
    const diff = known ? (p - base[0]) + (t - base[1]) : 0;
    const title = [tr("card.pt", { pt: text, base: c.base_pt.join("/") }), ...mods.map((x) => tr("bullet") + x), damage ? tr("card.damage", { n: damage }) : ""]
      .filter(Boolean).join("\n");
    return { text, damage, cls: diff > 0 ? " up" : diff < 0 ? " down" : "", title };
  }
  // 戦闘フェイズの間（と戦闘の欄）だけ出す
  const showPT = (extra) => !!ui.view && (ui.view.turn.phase === "combat" || (extra || "").includes("incombat"));
  function ptChip(c) {
    const pt = ptOf(c);
    if (!pt) return null;
    const chip = el("span", "ptchip" + pt.cls, pt.text);
    if (pt.damage) chip.append(el("span", "ptdmg", `−${pt.damage}`));
    chip.title = pt.title;
    return chip;
  }

  // Note と Link を、カードの上に出す短い文に（until は「まで」を付ける。Link の id は名前に）
  function cardTexts(c) {
    const out = (c.notes || []).map((n) => n.text + (n.until ? `（${n.until.replace(/_/g, " ")}）` : ""));
    for (const l of c.links || []) {
      const m = /^(\w+)(->|<-)(.*)$/.exec(l);
      if (!m) { out.push(l); continue; }
      if (ui.images && m[1] === "exiled_by") continue;
      const ids = m[3].split(",").map((id) => (ui.names[id] ? `<${displayName(ui.names[id])}>` : id));
      out.push(`${m[2] === "->" ? "→" : "←"} ${ids.join(", ")}（${m[1].replace(/_/g, " ")}）`);
    }
    return out;
  }

  function cardTile(c, extra) {
    const small = (extra || "").includes("small");
    const withArt = ui.images && !small;
    const d = el("div", "card" + (c.tapped ? " tapped" : "") + (c.land ? " land" : "") + (withArt ? " withart" : "") + (extra || ""));
    d.title = (c.name ? `<${displayName(c.name)}>` : tr("card.faceDown")) + ` ${c.id}`;
    d.dataset.ids = (c.ids || [c.id]).join(" ");  // カードの動き（motion.js）で前後の描画を対応づける
    const mark = ui.marks && ui.marks.get((c.ids || [c.id])[0]);
    if (mark) d.classList.add(mark);
    if (c.planned) d.classList.add("pv");  // 下書きで仮に出したカード（審判の処理の後に本物になる）
    d.onclick = (ev) => { if (!(hooks.card && hooks.card(c, ev))) showCard(c); };
    // 右クリック（タッチは長押し）は、対象選び・ブロック選びの最中でもいつでも詳細を出す
    d.oncontextmenu = (ev) => { ev.preventDefault(); ev.stopPropagation(); showCard(c); };
    const pt = showPT(extra) && !(extra || "").includes("under") ? ptChip(c) : null;
    if (withArt) {
      d.append(art(c, pt));
      return d;
    }
    if (pt) d.append(pt);
    d.append(el("div", "nm", (c.name ? `<${displayName(c.name)}>` : (c.face_down ? tr("card.faceDown") : "?")) + (c.count ? ` ×${c.count}` : "")));
    const flags = [c.id];
    if (c.token) flags.push("token");
    if (c.new) flags.push("new");
    if (c.face_down) flags.push("face down");
    if (c.controller && c.owner && c.controller !== c.owner) flags.push("owner " + c.owner);
    d.append(el("div", "id", flags.join(" · ")));
    for (const [k, n] of Object.entries(c.counters || {})) d.append(el("span", "chip", `${k} ×${n}`));
    for (const t of cardTexts(c)) {
      const ne = el("div", "note", t.length > 80 ? t.slice(0, 78) + "…" : t);
      ne.title = t;
      d.append(ne);
    }
    if (c.definition && !c.name) d.append(el("div", "note", JSON.stringify(c.definition)));
    return d;
  }

  let detailRequest = 0;
  async function showCard(c) {
    const request = ++detailRequest;
    const view = ui.view;
    document.querySelectorAll(".card.sel").forEach((x) => x.classList.remove("sel"));
    const lines = [c.name ? `<${displayName(c.name)}>  ${c.id}` : c.id];
    const state = [c.tapped && "tapped", c.token && "token", c.new && "new", c.face_down && "face down",
      c.controller && c.owner && c.controller !== c.owner && `owner ${c.owner}`].filter(Boolean);
    if (state.length) lines.push(state.join(" · "));
    for (const [k, n] of Object.entries(c.counters || {})) lines.push(`counter ${k} ×${n}`);
    for (const t of cardTexts(c)) lines.push("note: " + t);
    if (c.definition) lines.push("definition: " + JSON.stringify(c.definition));
    const card = c.name ? await source.oracle(c.name) : null;
    if (card) lines.push("", card.text || tr("card.noOracle"));
    if (request !== detailRequest || view !== ui.view) return;
    const detail = $("detail");
    $("fl-detail").hidden = false;
    detail.replaceChildren();
    if (ui.images && c.name && !c.face_down) {
      const img = el("img", "big");
      img.alt = displayName(c.name);
      img.src = imageURL(c.name);
      img.onerror = () => img.remove();
      detail.append(img);
    }
    detail.append(manaEl("div", "otext", lines.join("\n")));
    // 日本語版の文は印刷されたときの文。判定（審判）は英語のオラクルで行うので、英語も開けるようにする
    if (card && card.lang === "ja" && card.oracle) {
      const more = el("details", "oracle-en");
      more.append(el("summary", null, tr("card.oracleEn")), manaEl("div", "otext", card.oracle));
      detail.append(el("div", "muted onote", tr("card.printedNote")), more);
    }
    detail.classList.remove("muted");
  }

  // 同じ名前の土地（タップ状態ごと）は、少しずつずらして重ねた1つの束にする
  // アンタップの土地は名前ごとに、タップ状態の土地は名前に関係なく1つの束に
  function landGroups(lands, under) {
    const groups = [];
    for (const c of lands) {
      const key = c.tapped ? "tapped" : `${c.name}`;
      const g = groups.find((x) => x.key === key);
      if (g) g.cards.push(c); else groups.push({ key, cards: [c] });
    }
    return groups.map((g) => {
      const box = el("div", "lgroup" + (ui.images ? " overlap" : ""));
      box.dataset.key = `lands:${g.key}`;
      const cards = g.key === "tapped" ? [...g.cards].sort((a, b) => displayName(a.name).localeCompare(displayName(b.name))) : g.cards;
      box.append(...cards.map((c) => withUnder(c, under)));
      if (g.cards.length > 1) box.append(el("span", "chip strong gcount", `×${g.cards.length}`));
      return box;
    });
  }

  // 土地以外のパーマネントは種類ごとに分けて、左から クリーチャー・アーティファクト・エンチャント・
  // プレインズウォーカー・バトルの順に置く（種類の変わり目は少し空ける）
  const KIND_ORDER = ["creature", "artifact", "enchantment", "planeswalker", "battle", ""];
  function kindGroups(cards, under) {
    // 裏向きで種類が見えないものはクリーチャーとして置く
    const kindOf = (c) => c.kind || (c.face_down ? "creature" : "");
    const rank = (c) => { const i = KIND_ORDER.indexOf(kindOf(c)); return i < 0 ? KIND_ORDER.length : i; };
    const sorted = groupCards(cards).map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i);
    let prev = null;
    return sorted.map(({ c }) => {
      const tile = withUnder(c, under);
      const kind = kindOf(c);
      if (prev !== null && kind !== prev) tile.classList.add("kgap");
      tile.dataset.kind = kind || "other";
      prev = kind;
      return tile;
    });
  }

  // あるカードによって追放されたカード（exiled_by の Link）を、そのカードの下に差し込んだように出す。
  // 戦場のパーマネントに付いているオーラ・装備（attached の Link）も、付いている先のカードの下に出す
  // （コントローラーが違っても、付いている先の側に。attached は付いた側のカードの id の集合）
  function cardsUnder(v) {
    const exile = new Map((v.zones.exile.cards || []).map((c) => [c.id, c]));
    const bf = new Map((v.zones.battlefield.cards || []).map((c) => [c.id, c]));
    const under = {};
    const attached = new Set();
    const push = (host, c, cls) => { (under[host] = under[host] || []).push({ card: c, cls }); };
    for (const l of v.links) {
      if (l.kind === "exiled_by") {
        for (const t of l.targets) if (exile.has(t)) push(l.source, exile.get(t), " under");
      } else if (l.kind === "attached" && ui.images && bf.has(l.source) && bf.has(l.targets[0])
        && l.targets[0] !== l.source && !attached.has(l.targets[0])) {
        push(l.targets[0], bf.get(l.source), " under attached");
        attached.add(l.source);
      }
    }
    // 付いているオーラ・装備が追放したカード（幽霊による庇護など）は、付いている先の束の中で、
    // そのオーラのさらに奥に差し込む（オーラは自分の束を持たないので、そのままだとどこにも出ない）
    const expand = (list, seen) => list.flatMap((u) => {
      const id = u.card.id;
      if (!u.cls.includes("attached") || seen.has(id) || !under[id]) return [u];
      return [...expand(under[id], new Set([...seen, id])), u];
    });
    for (const host of Object.keys(under)) if (!attached.has(host)) under[host] = expand(under[host], new Set([host]));
    return { under, attached };
  }

  function withUnder(c, under) {
    const tile = cardTile(c);
    const cards = ui.images ? under[c.id] || [] : [];
    if (!cards.length) return tile;
    const box = el("div", "tuck");
    box.dataset.key = `tuck:${c.id}`;
    box.style.setProperty("--n", cards.length);
    if (cards.some((u) => u.cls.includes("attached"))) box.classList.add("hasaura");  // オーラは少し多めに見せる
    cards.forEach((u, i) => {
      const t = cardTile(u.card, u.cls);
      t.style.top = `calc(${i} * var(--h, 151px) * var(--peek, .12))`;
      box.append(t);
    });
    box.append(tile);
    return box;
  }

  // ライブラリー・墓地・追放をカードの束で表す。墓地と追放は一番上のカードを表に、クリックで中身を開く
  function pile(label, count, top, opts) {
    const p = el("div", "pile" + (count ? "" : " empty") + (opts.open ? " open" : ""));
    p.dataset.zone = opts.zone;
    if (opts.drop) p.dataset.drop = opts.drop;  // 手札から落とせる束（GUI の対局）
    const stackBox = el("div", "pstack");
    for (let i = Math.min(count, 4) - 1; i >= 1; i--) {
      const layer = el("div", "layer");
      layer.style.transform = `translate(${i * 2}px, ${i * 2}px)`;
      stackBox.append(layer);
    }
    const face = el("div", "face" + (top ? "" : " back") + (opts.known ? " known" : ""));
    if (top && ui.images && !broken.has(imageURL(top.name))) {
      const img = el("img");
      img.alt = displayName(top.name);
      const url = img.src = imageURL(top.name);
      img.onerror = (e) => {
        failed(url);
        const t = e.currentTarget, f = t.parentElement;
        t.remove();
        if (f) f.append(el("div", "ftext", `<${displayName(top.name)}>`));
      };
      face.append(img);
    } else if (top) {
      face.append(el("div", "ftext", `<${displayName(top.name)}>`));
    }
    if (count) stackBox.append(face);
    stackBox.append(el("span", "chip strong pcount", String(count)));
    stackBox.append(el("div", "plabel", label));  // 名前はカードの上に重ねる（縦の幅を取らない）
    if (opts.planned) {  // 下書き・送った依頼の予定（引く・見る・切削…）
      const chip = el("span", "chip pplan", tr("pile.planned"));
      chip.title = tr("pile.planned.title", { what: opts.planned });
      stackBox.append(chip);
    }
    p.append(stackBox);
    if (opts.note) p.title = (p.title ? p.title + "\n" : "") + opts.note;
    if (opts.onclick && count) p.onclick = opts.onclick;
    // 一番上を知っている: カードの表を出し、小さな印だけ付ける（束の幅は狭いので、長い文字はカードを隠す）
    if (opts.known) stackBox.append(el("span", "chip pknown", tr("pile.known")));
    if (top) p.title = tr(opts.known ? "pile.top.known" : "pile.top", { name: displayName(top.name) });
    return p;
  }

  function toggleOpen(key) {
    if (ui.open.has(key)) ui.open.delete(key); else ui.open.add(key);
    render();
  }

  // 開いた束の中身。画像で並べ、ライブラリーは上からの位置を重ねて出す
  function openedBlock(key, title, cards) {
    const z = el("div", "zone opened");
    z.dataset.key = `opened:${key}`;
    const close = el("button", "chip oclose", tr("opened.close"));
    close.title = tr("opened.close.title");
    close.onclick = () => toggleOpen(key);
    const h = el("h3", null, title);
    h.append(close);
    z.append(h);
    const row = el("div", "row");
    row.append(...(cards.length ? cards.map((c) => cardTile(c, ui.images ? " inhand" : " small"))
      : [el("div", "muted", tr("opened.none"))]));
    z.append(row);
    return z;
  }

  function libraryCards(lib) {
    return (lib.known_positions || []).map((c) => ({ ...c, pos: tr("opened.fromTop", { n: c.index + 1 }) }));
  }

  // 手札の並びは画面の上だけで持つ（ui.handOrder: pid → id の並び。卓の状態は変えない）。
  // 並べ替えたことのないカード（新しく引いたカード）は卓の順のまま後ろに付く
  function sortHand(pid, cards) {
    const order = (ui.handOrder || {})[pid];
    if (!order) return cards;
    const rank = new Map(order.map((id, i) => [id, i]));
    return cards.map((c, i) => [rank.has(c.id) ? rank.get(c.id) : order.length + i, c])
      .sort((a, b) => a[0] - b[0]).map(([, c]) => c);
  }

  // 手札の表のカードをドラッグで並べ替える。マウスは少し動かしたら、タッチは長押ししてから掴む
  // （タッチですぐ掴むと手札の横スクロールができなくなる）。離した後のクリックは捨てる。
  // 掴んだカードは写し（ghost）を画面の上に重ねてポインターに付いてこさせ（手札の欄ははみ出しを切るので）、
  // 元のカードは空いた場所の印として手札に残す。ほかのカードは空いた場所へ滑らせる。
  // GUI の対局では、同じ側の戦場・墓地・追放・ライブラリー（data-drop）へ落とすと、その予定を下書きに足す
  // （何をするかは hooks.drop が決め、領域に入ったら Tip で出す）。戦場のカード・Player・スタックの項目の上へ落とすと、
  // それを対象に唱える。
  // タッチで長押ししたまま動かさずに離したら、ほかのカードの長押しと同じく詳細を出す。
  // 描き直しは差分で当てるので、手札の欄とカードはドラッグの間も残り、盤面の更新はそのまま描く。押しているカードが
  // 更新で手札から無くなったら（捨てさせられたなど）、ドラッグはそこで終わる。
  // イベントは描き直しで消えない #top / #bottom で受ける（手札の欄は data-pid を持つ）
  const SLIDE = { duration: 160, easing: "cubic-bezier(.2, .7, .2, 1)" };
  const tilesOf = (row) => [...row.querySelectorAll(":scope > .card[data-ids]:not(.back)")];
  const orderOf = (row) => tilesOf(row).map((t) => t.dataset.ids);
  let drag = null;
  let droppedAt = -1e9;
  const finish = (commit) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    listen(false);
    clearTimeout(d.timer);
    if (!d.active) return;
    const action = commit && d.tile.isConnected ? d.action : null;
    overDrop(d, null, null);
    droppedAt = performance.now();
    const ghost = d.ghost;
    const order = (ui.handOrder ||= {});
    // 並べ替えを確定しないとき（領域へ落とした・取り消した）は、掴む前の並びに戻す
    const restore = () => { if (d.order) order[d.pid] = d.order; else delete order[d.pid]; };
    if (action) {  // 領域へ落とした: 手札の並びは元に戻して、予定を足す（描き直しで手札から仮に出ていく）
      ghost.remove();
      d.tile.classList.remove("dragging");
      restore();
      action.run();
      render();
      return;
    }
    if (!d.tile.isConnected) {  // 押していたカードが手札から無くなった
      ghost.remove();
      restore();
      return;
    }
    // 写しを空いた場所へ戻してから消す
    const to = d.tile.getBoundingClientRect();
    const back = ghost.animate([{ transform: ghost.style.transform },
      { transform: `translate(${to.left}px, ${to.top}px) scale(1)` }], SLIDE);
    const done = () => { ghost.remove(); d.tile.classList.remove("dragging"); };
    back.onfinish = back.oncancel = done;
    setTimeout(done, SLIDE.duration + 100);  // 画面が描かれていない（裏のタブなど）とアニメーションが終わらない
    if (commit) order[d.pid] = orderOf(d.row);
    else { restore(); setTimeout(render, SLIDE.duration + 120); }  // 戻るのを見せてから、元の並びで描く
    if (commit && d.touch && !d.moved) {
      ghost.remove();
      d.tile.classList.remove("dragging");
      d.tile.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    }
  };
  // 写しは、掴んだ所がポインターの下に来るように置く（領域の上では小さくする）
  const follow = (e) => {
    const d = drag;
    d.ghost.style.transform = `translate(${e.clientX - d.dx}px, ${e.clientY - d.dy - 14}px) scale(${d.action ? .8 : 1.06})`;
  };
  // ポインターの下の落とし先と、そこで何をするか。まず対象にできるもの（どちらの側でも戦場のカード・Player・
  // スタックの項目の data-target）: そこを対象に唱える。できなければ、同じ側の領域（data-drop）
  const dropAt = (e) => {
    if (!hooks.drop) return null;
    const id = drag.tile.dataset.ids.split(" ")[0];
    for (const t of document.elementsFromPoint(e.clientX, e.clientY)) {
      const target = t.dataset.target || (t.matches(".card[data-ids]") && t.closest("[data-drop='battlefield']") ? t.dataset.ids.split(" ")[0] : null);
      if (!target) continue;
      const action = hooks.drop(id, "target", target);
      if (action) return { el: t, action };
      break;
    }
    for (const t of drag.side.querySelectorAll("[data-drop]")) {
      const r = t.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) continue;
      const place = t.dataset.drop === "library" ? (e.clientY < r.top + r.height / 2 ? "library-top" : "library-bottom") : t.dataset.drop;
      const action = hooks.drop(id, place);
      return action ? { el: t, action } : null;
    }
    return null;
  };
  const tip = () => document.getElementById("droptip") || document.body.appendChild(Object.assign(el("div"), { id: "droptip" }));
  const overDrop = (d, hit, e) => {
    if (d.over && (!hit || hit.el !== d.over)) d.over.classList.remove("dropover");
    d.over = hit ? hit.el : null;
    d.action = hit ? hit.action : null;
    const t = tip();
    t.hidden = !hit;
    if (!hit) return;
    hit.el.classList.add("dropover");
    t.textContent = hit.action.label;
    t.style.left = `${e.clientX + 14}px`;
    t.style.top = `${e.clientY + 18}px`;
  };
  const grab = (e) => {
    const d = drag;
    d.active = true;
    d.order = (ui.handOrder || {})[d.pid] || null;
    const r = d.tile.getBoundingClientRect();
    d.dx = d.x0 - r.left;
    d.dy = d.y0 - r.top;
    d.ghost = d.tile.cloneNode(true);
    d.ghost.classList.add("dragghost");
    d.ghost.style.width = `${r.width}px`;
    d.ghost.style.height = `${r.height}px`;
    d.ghost.style.setProperty("--h", getComputedStyle(d.row).getPropertyValue("--h"));  // 小さい画面のカードの高さ
    document.body.append(d.ghost);
    d.tile.classList.add("dragging");
    try { d.tile.setPointerCapture(d.pointer); } catch { /* 離した後に長押しの時計が来たときなど */ }
    follow(e || { clientX: d.x0, clientY: d.y0 });
  };
  const onDown = (e) => {
    const tile = e.target.closest(".card");
    const row = tile && tile.parentElement;
    if (e.button !== 0 || !row || !row.matches(".hand[data-pid]") || !tilesOf(row).includes(tile)) return;
    // マウスは既定の動き（画像のドラッグ・文字の選択）を止める。画像のドラッグが始まるとポインターが取り消される
    if (e.pointerType !== "touch") e.preventDefault();
    finish(false);  // 前のドラッグが残っていたら片付ける
    drag = { tile, row, pid: row.dataset.pid, side: e.currentTarget, pointer: e.pointerId, x0: e.clientX, y0: e.clientY,
      active: false, touch: e.pointerType === "touch" };
    listen(true);
    if (drag.touch) drag.timer = setTimeout(() => drag && grab(), 300);
  };
  const onMove = (e) => {
    if (!drag || e.pointerId !== drag.pointer) return;
    // 離したのを取りこぼした（画面の外で離した・別の窓へ移った）: ボタンが押されていないなら元へ戻す
    if (e.pointerType === "mouse" && !(e.buttons & 1)) return finish(false);
    if (!drag.tile.isConnected) return finish(false);
    const moved = Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0);
    if (!drag.active) {
      if (drag.touch) { if (moved > 8) finish(false); return; }  // 長押しの前に動いたらスクロール
      if (moved < 5) return;
      grab(e);
    }
    if (moved > 8) drag.moved = true;
    const hit = dropAt(e);
    overDrop(drag, hit, e);
    // 手札の欄の上にいる間は、写しの中心が隣のカードの中心を越えたら、空いた場所を入れ替える
    const row = drag.row;
    const rr = row.getBoundingClientRect();
    if (!hit && e.clientY > rr.top - 40 && e.clientY < rr.bottom + 40) {
      const t = drag.tile;
      // 入れ替えの判定は、滑っている途中のずれ（translate）を除いた位置で比べる。見た目の位置で比べると、
      // 入れ替えた直後に元へ戻す判定になって行ったり来たりする
      const center = (o) => {
        const r = o.getBoundingClientRect();
        return r.left + r.width / 2 - (parseFloat(getComputedStyle(o).translate) || 0);
      };
      const mid = e.clientX - drag.dx + t.offsetWidth / 2;
      const others = tilesOf(row).filter((o) => o !== t);
      const next = others.find((o) => mid < center(o));
      const last = others[others.length - 1];
      if (last && (next ? t.nextElementSibling !== next : last.nextElementSibling !== t)) {
        const before = new Map(others.map((o) => [o, o.offsetLeft]));
        if (next) row.insertBefore(t, next); else last.after(t);
        // 途中の並びを覚えておく（ドラッグの間に描き直しても、この並びで描く。確定しなければ finish で戻す）
        (ui.handOrder ||= {})[drag.pid] = orderOf(row);
        fanTilt(row);  // 傾きを新しい並びの位置に合わせる
        for (const [o, x] of before) {  // 押し出されたカードは元の位置から滑らせる（FLIP。扇の transform とは別の translate で）
          const dx = x - o.offsetLeft;
          if (dx) o.animate([{ translate: `${dx}px 0` }, { translate: "0 0" }], SLIDE);
        }
      }
    }
    follow(e);
  };
  // 動かす・離す・取り消すは window で受ける（手札の欄の外や、ポインターの捕捉が外れた後でも届くように）。
  // 窓から離れた（別のアプリ・タブへ移った）ら元へ戻す。押している間だけ付ける
  const onUp = (e) => { if (drag && e.pointerId === drag.pointer) finish(true); };
  const onCancel = (e) => { if (drag && e.pointerId === drag.pointer) finish(false); };
  const onBlur = () => finish(false);
  const onHidden = () => { if (document.hidden) finish(false); };
  const listen = (on) => {
    const f = on ? "addEventListener" : "removeEventListener";
    noSelect(on);
    window[f]("pointermove", onMove, true);
    window[f]("pointerup", onUp, true);
    window[f]("pointercancel", onCancel, true);
    window[f]("blur", onBlur);
    document[f]("visibilitychange", onHidden);
  };
  for (const side of [$("top"), $("bottom")]) {
    side.addEventListener("pointerdown", onDown);
    side.addEventListener("dragstart", (e) => { if (e.target.closest(".hand")) e.preventDefault(); });
    // 落とした直後のクリック（離したカードの上で起きる）は捨てる
    side.addEventListener("click", (e) => {
      if (e.target.closest(".hand") && performance.now() - droppedAt < 300) { e.stopPropagation(); e.preventDefault(); }
    }, true);
    // 長押しで掴んだ後は、指を動かしても手札をスクロールさせない
    side.addEventListener("touchmove", (e) => { if (drag && drag.active) e.preventDefault(); }, { passive: false });
    // 長押しの途中に来るブラウザの contextmenu は捨てる（詳細は離したときに出す）
    side.addEventListener("contextmenu", (e) => { if (drag && e.target.closest(".hand")) { e.preventDefault(); e.stopPropagation(); } }, true);
  }

  // 扇の傾き: 中央からの位置を、端が ±3 になるように縮める（枚数が多くても傾きすぎない）。並べ替えた後にも呼ぶ
  function fanTilt(row) {
    const n = row.children.length, mid = (n - 1) / 2;
    [...row.children].forEach((c, i) => c.style.setProperty("--off", ((i - mid) * 3 / Math.max(3, mid)).toFixed(2)));
  }

  function handRow(v, pid) {
    const hand = v.zones[`${pid}.hand`];
    const shown = sortHand(pid, hand.cards || hand.known || []);
    const row = el("div", "hand" + (ui.images ? " fan" : ""));
    row.dataset.pid = pid;  // 手札のドラッグ（#top / #bottom で受ける）
    row.append(...shown.map((c) => cardTile(c, " inhand")));
    for (let i = shown.length; i < hand.count; i++) row.append(el("div", "card back inhand"));
    // 下書き・送った依頼で引く予定の「？」（中身は審判の処理の後に届く。play.js の planned）
    const extra = (ui.planned && ui.planned.hand[pid]) || 0;
    for (let i = 0; i < extra; i++) {
      const q = el("div", "card back inhand planned", "？");
      q.title = tr("hand.toDraw.title");
      row.append(q);
    }
    fanTilt(row);
    const box = el("div", "handbox");
    box.dataset.key = `hand:${pid}`;
    box.dataset.zone = `${pid}.hand`;
    box.append(row);  // 枚数は出さない（カードの裏・表と「？」で分かる）
    return box;
  }

  // ライフ: 「LIFE」と数のプレート。5 以下は警告色、0 以下は敗北の色。増減はダメージ・回復の演出で見せる（motion.js）
  function lifeBadge(p) {
    const level = p.life <= 0 ? " dead" : p.life <= 5 ? " low" : "";
    const box = el("span", "life" + level);
    box.title = tr("player.life.title", { life: p.life });
    box.append(el("span", "llbl", "LIFE"), el("span", "lnum", String(p.life)));
    const wrap = el("span", "lifewrap");
    wrap.append(box);
    return wrap;
  }

  // 宣言は、その Player の名前の横に吹き出しで出す。今のターン・フェイズ・ステップでの最後の宣言だけ（審判への依頼は Live のときだけ）。
  // パスは優先権が続けてパスされている間だけ（スタックが変わる・ステップが進むと消える。turn.passed と同じ）
  const DECLS = ["pass", "keep", "mulligan", "no_block", "concede", "intent", "ask", "answer", "ruled"];
  // 出現のアニメーションは、その位置で新しく出た宣言だけ（続いている宣言を描き直すたびに動かさない）
  // 審判の処理（ruled）は、出てから RULED_MS の間だけ出して消す（ステップの間ずっと出したままにしない）。
  // 過去の盤面を見ているときは、その位置で新しく出たときだけ
  const RULED_MS = 7000;
  const ruledSeen = new Map();  // ruled の seq → Live で最初に出した時刻
  function speech(v, pid, prev) {
    const t = v.turn;
    const d = v.declarations.filter((x) => x.player === pid && x.turn === t.turn && x.step === t.step
      && (!x.phase || x.phase === t.phase)).pop();
    if (!d || (d.kind === "pass" && !t.passed.includes(pid))) return null;
    if (d.kind === "intent" && !ui.live) return null;  // 審判への依頼は、過去の盤面（履歴の再生）では出さない
    const freshHere = !prev || !prev.declarations.some((x) => x.seq === d.seq);
    let ruledAge = null;
    if (d.kind === "ruled") {
      if (!ui.live) {
        if (!freshHere) return null;
      } else {
        if (!ruledSeen.has(d.seq)) ruledSeen.set(d.seq, Date.now());
        ruledAge = Date.now() - ruledSeen.get(d.seq);
        if (ruledAge >= RULED_MS) return null;
      }
    }
    const word = DECLS.includes(d.kind) ? tr(`decl.${d.kind}`) : d.kind;
    // 審判への依頼は長いので、吹き出しには種類だけ（中身は操作パネルと Log に出る）
    const text = d.text && d.kind !== "intent" && !d.text.startsWith("standing") ? tr("decl.with", { word, text: localize(d.text) }) : word;
    const fresh = freshHere && !ruledAge;
    const b = el("span", "speech" + (fresh ? " fresh" : "") + (ruledAge !== null ? " fading" : ""));
    b.dataset.key = `speech:${d.seq}`;  // 新しい宣言は新しい要素で（出たときの動きを出す）
    // 消えるまでの残りの時間（描き直しても、出た時刻から数える）
    if (ruledAge !== null) b.style.animationDelay = `${fresh ? "0ms, " : ""}${-ruledAge}ms`;
    b.append(el("span", "stx", text));
    b.title = `${d.player} ${d.kind}${d.text ? "：" + localize(d.text) : ""}`;
    return b;
  }

  function playerSide(v, pid, mirrored, prev) {
    const p = v.players.find((x) => x.id === pid);
    const box = el("div");
    const head = el("div", "phead");
    head.append(avatar(v, p), lifeBadge(p));
    if (hooks.player) {
      head.classList.add("clickable");
      head.onclick = (ev) => hooks.player(pid, ev);
      head.dataset.target = pid;  // 手札から落として対象にする（手札のドラッグ）
    }
    if (ui.marks && ui.marks.has(pid)) head.classList.add(ui.marks.get(pid));
    // ライフの横に優先権（アイコン）。欄はいつも取っておく（持ち主が替わってもフェイズ以降の並びがずれないように）
    const prio = el("span", "prio" + (v.turn.priority === pid ? "" : " off"));
    prio.title = tr("player.priority");
    prio.setAttribute("aria-label", prio.title);
    prio.append(icon(PRIORITY_ICON));
    head.append(prio);
    // アクティブ Player は、その横に今のフェイズ
    if (v.turn.active === pid && v.turn.turn > 0) head.append(phaseStrip(v.turn));
    if (ui.thinker === pid) {
      const think = el("span", "chip think", tr("player.thinking"));
      think.append(dots());
      head.append(think);
    }
    if (ui.mymove && ui.play.seat === pid) head.append(el("span", "chip yours", tr("player.yourMove")));
    // 残り（状態・マナ・カウンターなど）は右の余白の中だけに並べ、入らない分は切る（左の並びを押さない）
    const extra = el("span", "pextra");
    head.append(extra);
    if (p.status && p.status !== "playing") extra.append(el("span", "status", p.status));
    if (v.turn.active === pid && v.turn.turn === 0) extra.append(el("span", "chip", tr("player.onThePlay")));
    for (const [k, n] of Object.entries(p.counters || {})) extra.append(el("span", "chip", `${k} ×${n}`));
    for (const m of p.mana) {
      const chip = el("span", "chip mana");
      chip.append(...manaNodes(`{${m.color}}`), document.createTextNode(` ×${m.amount}`));
      if (hooks.mana) {  // GUI の対局: 押すと使う予定を足すメニュー（Player の欄のメニューは出さない）
        chip.classList.add("clickable");
        chip.onclick = (ev) => { if (hooks.mana(pid, m, ev)) ev.stopPropagation(); };
      }
      if (m.notes) {
        const t = m.notes.map((n) => n.text).join("; ");
        chip.append(document.createTextNode(tr("paren", { text: t.length > 40 ? t.slice(0, 38) + "…" : t })));
        chip.title = t;
      }
      extra.append(chip);
    }
    for (const n of p.notes || []) extra.append(el("span", "chip", n.text));
    extra.title = [...extra.children].map((c) => c.title || c.textContent).join("\n");

    // 束: ライブラリー・墓地・追放
    const piles = el("div", "piles");
    const lib = v.zones[`${pid}.library`];
    const libKey = `${pid}.library`;
    const pileClick = (key) => (ev) => { if (!(hooks.pile && hooks.pile(key, ev))) toggleOpen(key); };
    // 一番上を知っている（公開した・見た）ときは束の表に出す。全知の審判の席では出さない（いつも見えてしまう）
    const libTop = v.viewer ? (lib.known_positions || []).find((c) => c.index === 0) : null;
    piles.append(pile(tr("pile.library"), lib.count, libTop,
      { known: !!libTop, note: (lib.known_positions || []).length ? tr("pile.knownPositions", { n: lib.known_positions.length }) : "",
        planned: ((ui.planned && ui.planned.piles[libKey]) || []).join(tr("play.plannedSep")),
        open: ui.open.has(libKey), onclick: pileClick(libKey), zone: libKey, drop: "library" }));
    const gy = v.zones[`${pid}.graveyard`];
    const gyKey = `${pid}.graveyard`;
    piles.append(pile(tr("pile.graveyard.short"), gy.count, (gy.cards || [])[0], { open: ui.open.has(gyKey), onclick: pileClick(gyKey), zone: gyKey, drop: "graveyard" }));
    const ex = (v.zones.exile.cards || []).filter((c) => c.owner === pid);
    const exKey = `${pid}.exile`;
    // GUI の対局では、空でも手札から落とせるように出す
    if (ex.length || hooks.drop) piles.append(pile(tr("pile.exile"), ex.length, ex[ex.length - 1], { open: ui.open.has(exKey), onclick: pileClick(exKey), zone: exKey, drop: "exile" }));

    // 戦場: 土地以外と土地（土地は名前ごとに束ねる）
    const { under, attached } = cardsUnder(v);
    const bf = (v.zones.battlefield.cards || []).filter((c) => (c.controller || c.owner) === pid && !attached.has(c.id));
    const others = el("div", "row bfrow"); others.append(...kindGroups(bf.filter((c) => !c.land), under));
    const lands = el("div", "row bfrow lands"); lands.append(...landGroups(bf.filter((c) => c.land), under));
    const field = el("div", "field");
    field.dataset.drop = "battlefield";
    field.append(...(mirrored ? [lands, others] : [others, lands]));

    // 左の列に束（ライブラリー・墓地・追放）、右に戦場・土地・手札（相手の欄は上下を反転）
    const hand = handRow(v, pid);
    const main = el("div", "sidemain");
    main.append(...(mirrored ? [hand, field] : [field, hand]));
    const grid = el("div", "sidegrid");
    grid.append(piles, main);

    // 開いた墓地・追放の中身
    const opened = [];
    if (ui.open.has(libKey) && !lib.collapsed) {
      opened.push(openedBlock(libKey, tr("opened.library", { count: lib.count }), libraryCards(lib)));
    }
    if (ui.open.has(gyKey) && gy.count) {
      opened.push(openedBlock(gyKey, tr("opened.graveyard", { count: gy.count }), (gy.cards || []).map((c, i) => ({ ...c, pos: String(i + 1) }))));
    }
    if (ui.open.has(exKey) && ex.length) opened.push(openedBlock(exKey, tr("opened.exile", { count: ex.length }), ex));

    box.append(head, grid, ...opened);
    // 宣言の吹き出しは見出しから戦場の側へはみ出す（位置は placeSpeech で合わせる）
    const said = speech(v, pid, prev);
    if (said) box.append(said);
    return box;
  }

  // 吹き出しを Player のアイコンの真下に置き、しっぽ（左から 18px）をアイコンの中央に合わせる（描いた後に測る）
  // 手札（扇）は横にスクロールさせず、重なりを詰めて幅に収める。スクロールする欄は縦にもはみ出せず、浮かせた・傾けた・
  // 大きくしたカードが切れるので、欄は overflow: visible のまま（--ov が詰めた重なり。無ければ CSS の既定の重なり）。
  // 枚数が変わったら描くたびに、幅が変わったら（窓・Log の欄）ResizeObserver で合わせ直す
  const handFit = new ResizeObserver((entries) => entries.forEach((e) => fitHand(e.target)));
  function fitHand(row) {
    row.style.removeProperty("--ov");
    const cards = row.children, n = cards.length;
    if (n < 2) return;
    const cs = getComputedStyle(row);
    const room = row.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const first = cards[0], last = cards[n - 1];
    if (last.offsetLeft - first.offsetLeft + last.offsetWidth <= room) return;  // 並びの幅（扇の傾きは数えない）
    const w = Math.max(...[...cards].map((c) => c.offsetWidth));
    const gap = parseFloat(cs.columnGap) || 0;  // 欄の gap も1枚ごとの送りに入る
    row.style.setProperty("--ov", `${Math.floor((room - w) / (n - 1) - w - gap)}px`);
  }
  const handWatched = new WeakSet();
  function fitHands() {
    for (const row of document.querySelectorAll(".hand.fan")) {
      if (!handWatched.has(row)) {  // 画像が読み込まれるとカードの幅が変わる（欄の幅は変わらないので ResizeObserver では拾えない）
        handWatched.add(row);
        handFit.observe(row);
        row.addEventListener("load", () => fitHand(row), true);
      }
      fitHand(row);
    }
  }

  function placeSpeech() {
    for (const b of document.querySelectorAll(".speech")) {
      const icon = b.parentElement.querySelector(".phead .avatar");
      if (!icon) continue;
      const box = b.parentElement.getBoundingClientRect(), r = icon.getBoundingClientRect();
      b.style.left = `${r.left + r.width / 2 - box.left - 18}px`;  // 左の余白へ少しはみ出してよい
      b.style.top = `${r.bottom - box.top + 6}px`;
    }
  }

  // 戦闘・スタックの小窓を一時的に畳む（下の盤面を見るため）。畳んだときの中身（sig）から変わったら、自動で開く
  function foldable(boxId, sig, count) {
    const box = $(boxId);
    const btn = box.querySelector(".bxmin");
    btn.onclick = () => {
      const min = !box.classList.contains("min");
      box.classList.toggle("min", min);
      box.dataset.sig = min ? box.dataset.cur : "";
      btn.textContent = min ? "＋" : "–";
      btn.title = tr(min ? "box.open.title" : "box.min.title");
      box.querySelector(".bxcount").textContent = min ? tr("paren", { text: box.dataset.count }) : "";
    };
    box.dataset.cur = sig;
    box.dataset.count = count;
    if (box.classList.contains("min") && box.dataset.sig !== sig) btn.onclick();
    box.querySelector(".bxcount").textContent = box.classList.contains("min") ? tr("paren", { text: count }) : "";
    btn.title = tr(box.classList.contains("min") ? "box.open.title" : "box.min.title");  // 言語を変えた後の描き直しでも合わせる
  }

  // スタック: 上から順に、呪文はそのカード、能力は発生源のカードを小さく出し、横に種類・文・対象を並べる
  const KINDS = ["spell", "activated", "triggered", "ability"];

  // anim: 動かすときだけ { duration, forward }（積む・解決の出入りは stack.js）
  function renderStack(v, anim) {
    foldable("fl-stack", v.stack.map((s) => s.id).join(","), v.stack.length);
    const cards = new Map();
    for (const z of Object.values(v.zones)) for (const c of z.cards || []) cards.set(c.id, c);
    stack.update(v.stack.map((s, i) => {
      const li = el("li", "sitem" + (s.kind === "spell" ? "" : " ability"));
      li.dataset.key = s.id;
      const id = s.card || s.source;
      const c = cards.get(id) || (id && ui.names[id] ? { id, name: ui.names[id] } : null);
      const pic = c ? cardTile({ ...c, tapped: false, counters: {}, notes: [], links: [] }, ui.images ? " instack" : " small")
        : el("div", "card small", "?");
      if (s.kind !== "spell") pic.classList.add("src");
      if (ui.marks && ui.marks.has(s.id)) li.classList.add(ui.marks.get(s.id));
      if (s.planned) li.classList.add("pv");
      // 手札から落として対象にする（手札のドラッグ）。下書きで仮に積んだ呪文はカードで指す（仮の能力は指せない）
      const target = s.planned ? s.card : s.id;
      if (target) li.dataset.target = target;
      if (hooks.stack) {
        // スタックの項目は、カードの絵を押しても項目として扱う（能力の発生源のカードのメニューを出さない）
        li.onclick = (ev) => { if (!hooks.stack(s, i, ev) && c) showCard(c); };
        pic.onclick = (ev) => { ev.stopPropagation(); li.onclick(ev); };
      }
      if (c) li.oncontextmenu = (ev) => { ev.preventDefault(); ev.stopPropagation(); showCard(c); };
      const body = el("div", "sbody");
      const head = el("div", "shead");
      head.append(el("span", "chip" + (i === 0 ? " strong" : ""), i === 0 ? tr("stack.top") : String(i + 1)),
        el("strong", null, ` ${KINDS.includes(s.kind) ? tr(`stack.${s.kind}`) : s.kind}`), document.createTextNode(tr("stack.controller", { who: s.controller })));
      body.append(head);
      if (c && c.name) body.append(el("div", "sname", (s.kind === "spell" ? "" : tr("stack.source")) + `<${displayName(c.name)}>`));
      if (s.text) {
        const t = manaEl("div", "stext", localize(s.text));
        t.title = localize(s.text);
        body.append(t);
      }
      const targets = v.links.filter((l) => l.source === s.id && l.kind === "target").flatMap((l) => l.targets);
      if (targets.length) {
        const tg = el("div", "stargets");
        tg.append(el("span", "muted", tr("stack.targets")), ...targets.map((t) => el("span", "chip", ui.names[t] ? `<${displayName(ui.names[t])}>` : t)));
        body.append(tg);
      }
      li.append(pic, body);
      li.title = s.id;
      return li;
    }), anim);
  }

  // 戦闘: 攻撃クリーチャーごとに、カード・攻撃先・ブロックしているカードを縦に並べる
  function renderCombat(v) {
    const box = $("combat");
    $("combatbox").hidden = !v.combat.attacks.length;
    foldable("combatbox", JSON.stringify([v.combat.attacks, v.combat.blocks, (ui.planned && ui.planned.blocks) || []]),
      v.combat.attacks.length);
    if (!v.combat.attacks.length) {
      patch(box, []);
      return;
    }
    const cards = new Map((v.zones.battlefield.cards || []).map((c) => [c.id, c]));
    const tile = (id) => (cards.has(id) ? cardTile(cards.get(id), ui.images ? " incombat" : " small")
      : el("div", "card small", ref(id)));
    patch(box, v.combat.attacks.map((a) => {
      const col = el("div", "cbcol");
      col.dataset.key = a.attacker;
      // 攻撃先は、画面の上下のどちらの Player かを矢印で（Player 以外はカード名）
      // プレインズウォーカーはコントローラーの側（バトルは守る Player が卓に無いので →）
      const pw = cards.get(a.target);
      const side = pw && pw.attackable === "planeswalker" ? pw.controller || pw.owner : a.target;
      const arrow = side === ui.sides.top ? "↑" : side === ui.sides.bottom ? "↓" : "→";
      const to = el("div", "cbto");
      to.append(el("span", "arw", arrow), document.createTextNode(cards.has(a.target) || ui.names[a.target]
        ? ` <${ui.names[a.target] ? displayName(ui.names[a.target]) : a.target}>` : ` ${a.target}`));
      to.title = tr("combat.target.title", { target: ref(a.target) });
      // 下書きの攻撃は、攻撃先を押して変えられる（相手のプレインズウォーカー・自分のバトルがあるとき）
      const change = hooks.retarget && hooks.retarget(a);
      if (change) {
        to.classList.add("retarget");
        to.title += tr("combat.retarget.title");
        to.onclick = (ev) => { ev.stopPropagation(); change(); };
      }
      // ブロック側は防御側の Player の位置に合わせる（上の Player を攻撃するなら、ブロックは攻撃カードの上）
      const defense = [];
      const blockers = v.combat.blocks.filter((b) => b.attacker === a.attacker);
      // 下書き・送った依頼のブロック（まだ審判が書いていない）は「予定」として薄く出す
      const plannedBlocks = ((ui.planned && ui.planned.blocks) || []).filter((b) => b.attacker === a.attacker
        && !blockers.some((x) => x.blocker === b.blocker));
      if (blockers.length || plannedBlocks.length) {
        const row = el("div", "cbrow");
        row.append(...blockers.map((b) => tile(b.blocker)), ...plannedBlocks.map((b) => {
          const t = tile(b.blocker);
          t.classList.add("plannedblk");
          return t;
        }));
        defense.push(el("div", "cbblk", tr(blockers.length ? "combat.blockedBy" : "combat.blockedBy.planned")), row);
      } else if (["declare_blockers", "combat_damage", "first_strike_damage", "end_of_combat"].includes(v.turn.step)) {
        defense.push(el("div", "cbblk muted", tr("combat.noBlocks")));
      }
      if (arrow === "↑") col.append(...defense.reverse(), to, tile(a.attacker));
      else col.append(tile(a.attacker), to, ...defense);
      return col;
    }));
  }

  // ---------------------------------------------------------------- ターンとフェイズ

  const SVG = "http://www.w3.org/2000/svg";

  // フェイズのアイコン（線画の SVG。色は文字色に合わせる）
  const PHASE_ICON = {
    beginning: "M4 17h16M7 17a5 5 0 0 1 10 0M12 4v3M5.6 8.6l2.1 2.1M18.4 8.6l-2.1 2.1M2 13h3M19 13h3",  // 日の出
    main1: "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM9 7h6M9 10h6",        // カード
    combat: "M4 4l11 11M15 15l-2 3M15 15l3-2M20 4L9 15M9 15l2 3M9 15l-3-2M3 21l3-3M21 21l-3-3",          // 剣を交差
    main2: "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM9 7h6M9 10h6",
    ending: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",                                          // 月
  };
  const PRIORITY_ICON = "M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11.5v-8a1.5 1.5 0 0 1 3 0V12M14 11.5V5a1.5 1.5 0 0 1 3 0v7"
    + "M17 9.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1.5a7 7 0 0 1-5.6-2.8L3.3 14.6a1.5 1.5 0 0 1 2.4-1.8L8 15.5";  // 挙げた手
  const PHASES = ["beginning", "main1", "combat", "main2", "ending"];
  const STEPS = ["untap", "upkeep", "draw", "main", "beginning_of_combat", "declare_attackers", "declare_blockers",
    "combat_damage", "first_strike_damage", "end_of_combat", "end", "cleanup"];

  function icon(d) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
    return svg;
  }

  // Player のアイコン: 人の席は旅の少年、AI の席は石のゴーレム（どちらか分からない観戦では少年）。名前は Tip に
  function avatar(v, p) {
    const humans = ui.idle && ui.idle.humans;
    const kind = humans && !humans.includes(p.id) ? "ai" : "human";
    const a = el("span", "avatar" + (ui.play && ui.play.seat === p.id ? " me" : ""));
    a.title = tr("player.name", { name: p.name, id: p.id }) + tr(`player.kind.${kind}`);
    a.setAttribute("aria-label", a.title);
    const img = el("img");
    img.src = `static/avatar-${kind}.png`;
    img.alt = "";
    img.draggable = false;
    a.append(img);
    return a;
  }

  function phaseStrip(t) {
    const strip = el("span", "phases");
    strip.title = `${t.phase}/${t.step}`;
    for (const ph of PHASES) {
      const cur = ph === t.phase;
      const p = el("span", "ph" + (cur ? " cur" : ""));
      p.title = tr(`phase.${ph}`);
      p.append(icon(PHASE_ICON[ph]));
      if (cur) p.append(el("span", "pstep", STEPS.includes(t.step) ? tr(`step.${t.step}`) : t.step));
      strip.append(p);
    }
    return strip;
  }

  // 戦闘ダメージのステップと、その前のステップ（ここから進んだときだけぶつけ合う）
  const INTO_DAMAGE = { first_strike_damage: ["declare_attackers", "declare_blockers"],
    combat_damage: ["declare_attackers", "declare_blockers", "first_strike_damage"] };

  // スタックから無くなった項目（進めたときだけ）を、解決として光らせ、対象へ光を飛ばす。
  // 対象の位置は今の画面で、無くなった（死亡など）なら描く前の位置で
  function resolveFx(shown, v, before, stackRects) {
    const items = [];
    for (const s of shown.stack) {
      if (s.planned || v.stack.some((x) => x.id === s.id)) continue;
      const from = stackRects.get(s.id);
      if (!from || !from.width) continue;
      const targets = [], flash = [];
      for (const id of shown.links.filter((l) => l.source === s.id && l.kind === "target").flatMap((l) => l.targets)) {
        const slot = ui.sides.top === id ? "top" : ui.sides.bottom === id ? "bottom" : null;
        const e = slot ? $(slot).querySelector(".phead .avatar")
          : document.querySelector(`#top [data-ids~="${CSS.escape(id)}"], #bottom [data-ids~="${CSS.escape(id)}"]`);
        const rect = e && e.getBoundingClientRect();
        if (rect && rect.width) {
          targets.push(rect);
          flash.push(e);
        } else if (before.cards.has(id)) targets.push(before.cards.get(id).rect);
      }
      items.push({ from, targets, flash });
    }
    if (items.length) motion.resolve(items);
    return items.length > 0;
  }

  // 解決する項目（進めたときだけ）の対象（カード・Player の id）
  function resolvingIds(shown, v) {
    return new Set(shown.stack.filter((s) => !s.planned && !v.stack.some((x) => x.id === s.id))
      .flatMap((s) => shown.links.filter((l) => l.source === s.id && l.kind === "target").flatMap((l) => l.targets)));
  }
  const cardEls = (id) => {
    const q = CSS.escape(id);
    return [...document.querySelectorAll(`#top [data-ids~="${q}"], #bottom [data-ids~="${q}"], #combatbox [data-ids~="${q}"]`)];
  };

  const slotOf = (id) => (ui.sides.top === id ? "top" : ui.sides.bottom === id ? "bottom" : null);

  // 効果が出る要素: カードは盤面と戦闘の欄の写し、Player はライフ。描く前に写しを取り、効果（P/T・ダメージ・ライフ）は
  // 光の玉が当たってから・ぶつかってから見せる（motion.hold / release）
  const effectEls = (ids) => [...ids].flatMap((id) => {
    const slot = slotOf(id);
    return slot ? [$(slot).querySelector(".phead .lifewrap")].filter(Boolean) : cardEls(id);
  });

  // ダメージと回復（進めたときだけ）: 戦場のカードの負ったダメージが増えた・Player のライフが減った・増えた。
  // id → 減った数（回復は負の数）
  function damaged(shown, v) {
    const field = (view) => new Map(((view.zones.battlefield || {}).cards || []).map((c) => [c.id, c]));
    const was = field(shown);
    const out = new Map();
    for (const [id, c] of field(v)) {
      const amount = was.has(id) ? damageOf(c) - damageOf(was.get(id)) : 0;
      if (amount > 0) out.set(id, amount);
    }
    for (const p of v.players) {
      const prev = shown.players.find((x) => x.id === p.id);
      if (prev && p.life !== prev.life) out.set(p.id, prev.life - p.life);
    }
    return out;
  }

  // ダメージ・回復の演出（描いた後）。解決の対象なら光の玉が当たったとき、戦闘でぶつけ合ったならぶつかったとき
  // （絆魂の回復も）、ほかはすぐ
  function damageFx(dmg, targeted, clashed) {
    const hits = [...dmg].map(([id, amount]) => {
      const at = targeted.has(id) ? motion.timing.hit : clashed ? motion.timing.clash : 0;
      const slot = slotOf(id);
      if (!slot) return { cards: cardEls(id), amount, at };
      const head = $(slot).querySelector(".phead");
      return head && { side: $(slot), avatar: head.querySelector(".avatar"), life: head.querySelector(".life"), amount: Math.abs(amount), heal: amount < 0, at };
    }).filter(Boolean);
    if (hits.length) motion.damage(hits);
  }

  // 描き直しは、組み立てた木を今の DOM に差分で当てる（patch.js）。使い回した要素の状態（選んだ文字・フォーカス・
  // 打ちかけの文字・開いた折りたたみ・スクロール・手札のドラッグ）は更新で消えない
  function render() {
    // GUI の対局では、下書きの行を仮に反映した写しを描く（hooks.preview。卓の view は変えない）
    const v = ui.view && hooks.preview ? hooks.preview(ui.view) : ui.view;
    if (!v) return;
    // カードの動きは、呼び出し元が ui.animate を立てた描画（1件ずつの移動・自動再生）だけ
    const animate = ui.animate && ui.motion ? ui.animate : null;
    ui.animate = null;
    motion.stop();
    stack.stop();
    const before = animate && ui.shown ? motion.capture() : null;
    const shown = ui.shown;
    // 解決の演出に使う、描く前のスタックの項目の位置（項目の id → カードの絵の位置）
    const stackRects = before ? new Map([...$("stack").children].filter((li) => li.dataset.key)
      .map((li) => [li.dataset.key, (li.querySelector(".card") || li).getBoundingClientRect()])) : null;
    const forward = before && shown && v !== shown && v.version > shown.version;
    const targeted = forward ? resolvingIds(shown, v) : new Set();
    const dmg = forward ? damaged(shown, v) : new Map();
    // 戦闘ダメージに進んだら（戦闘の欄が開いていればぶつけ合う）、ダメージもぶつかってから見せる
    const cb = $("combatbox");
    const willClash = forward && (INTO_DAMAGE[v.turn.step] || []).includes(shown.turn.step) && !cb.hidden && !cb.classList.contains("min");
    const heldHit = forward ? motion.hold(effectEls(targeted)) : [];
    const heldClash = willClash ? motion.hold(effectEls([...dmg.keys()].filter((id) => !targeted.has(id)))) : [];
    ui.names = collectNames(v);
    cardNames.want(Object.values(ui.names));
    ui.attacking = new Set(v.combat.attacks.map((a) => a.attacker));
    ui.blocking = new Set(v.combat.blocks.map((b) => b.blocker));
    const t = v.turn;
    // ターンは、アクティブ Player の何ターン目か（ターンは席の順に回る）。通算は Tip に
    const n = Math.ceil(t.turn / Math.max(1, v.players.length));
    $("turn").textContent = t.turn === 0 ? tr("turn.pregame")
      : ui.play && t.active === ui.play.seat ? tr("turn.mine", { n }) : tr("turn.theirs", { who: t.active, n });
    $("turn").title = t.turn === 0 ? "" : tr("turn.title", { turn: t.turn });
    // 最新で、自分以外（審判・AI・相手）の番を待っている間は、動く印を出す
    const thinker = ui.live && t.waiting_on && !(ui.play && t.waiting_on === ui.play.seat) ? t.waiting_on : null;
    ui.thinker = thinker;
    const thinking = $("thinking");
    // 自分の番（操作待ち）は、同じ所に緑の印。番が回ってきた瞬間だけ、自分の欄と操作パネルを光らせる
    const mymove = ui.live && !!ui.play && t.waiting_on === ui.play.seat;
    const turned = mymove && !ui.mymove && !!ui.shown;
    ui.mymove = mymove;
    thinking.hidden = !thinker && !mymove;
    thinking.classList.toggle("yours", mymove);
    if (thinker) patch(thinking, [thinker === "judge" ? tr("thinking.judge") : tr("thinking.player", { who: thinker }), dots()]);
    else if (mymove) patch(thinking, [tr("thinking.mine")]);
    document.body.classList.toggle("judging", thinker === "judge");
    document.body.classList.toggle("mymove", mymove);
    document.title = (mymove ? "● " : "") + document.title.replace(/^● /, "");  // 別のタブにいても番が来たとわかる
    if (turned) {
      document.body.classList.remove("turnflash");
      void document.body.offsetWidth;  // 続けて回ってきても、もう一度光らせる
      document.body.classList.add("turnflash");
    }

    // 視点の Player を下に。judge は先攻を上に
    const order = v.players.map((p) => p.id);
    const me = v.viewer && order.includes(v.viewer) ? v.viewer : order[order.length - 1];
    const opp = order.find((p) => p !== me) || me;
    ui.sides = { top: opp, bottom: me };
    const from = ui.deltaBase && ui.deltaBase.pos === ui.pos ? ui.deltaBase.from : ui.pos - 1;
    for (const [slot, pid] of [["top", opp], ["bottom", me]]) {
      const side = $(slot);
      patch(side, [playerSide(v, pid, slot === "top", from >= 0 && from < ui.pos ? viewAt(from) : null)]);
      side.classList.toggle("active", t.active === pid);
    }
    fitHands();
    // 押していた手札のカードが、この更新で手札から無くなった
    if (drag && !drag.tile.isConnected) finish(false);

    renderStack(v, before && shown !== v ? { duration: animate.duration, forward: v.version > shown.version } : null);

    renderCombat(v);

    const pos = $("pos");
    pos.max = ui.cursor;
    pos.value = ui.pos;
    const posText = $("posText");
    posText.textContent = tr("replay.position.short", { pos: ui.pos, cursor: ui.cursor });
    posText.title = tr("replay.position", { pos: ui.pos, cursor: ui.cursor, version: v.version });
    $("live").classList.toggle("on", ui.live);
    renderLog();
    placeSpeech();
    clampFloats();
    ui.shown = v;
    // 同じ更新で呪文・能力が解決したら、除去の演出は解決の光が対象に当たってから
    const resolved = before && shown !== v && shown.stack.some((s) => !s.planned && !v.stack.some((x) => x.id === s.id));
    if (before && shown !== v) motion.play(before, shown, v, animate.duration, resolved ? 420 : 0);
    // 戦闘ダメージのステップに進んだら、戦闘の欄でカードをぶつけ合う（戻るときは出さない）
    const clashed = before && shown !== v && (INTO_DAMAGE[t.step] || []).includes(shown.turn.step) && motion.clash();
    if (forward) {
      const played = resolveFx(shown, v, before, stackRects);
      motion.release(heldHit, played ? motion.timing.hit : 0);
      motion.release(heldClash, clashed ? motion.timing.clash : 0);
      damageFx(dmg, played ? targeted : new Set(), clashed);
    }
    if (hooks.after) hooks.after();
  }

  function renderLog() {
    if (!document.body.classList.contains("log-open")) return;  // 閉じているときは作らない
    const box = $("log");
    if (ui.seat !== "judge" && !ui.play) {
      patch(box, [el("div", "batch muted", tr("log.judgeOnly"))]);
      return;
    }
    const groups = [];
    for (const e of ui.log) {
      const last = groups[groups.length - 1];
      if (last && last.batch === e.batch) last.items.push(e);
      else groups.push({ batch: e.batch, items: [e] });
    }
    patch(box, groups.map((g) => {
      const d = el("div", "batch");
      d.dataset.key = `b${g.batch}`;
      const first = g.items[0];
      const actors = [...new Set(g.items.map((e) => e.actor))].join(",");
      d.append(manaEl("div", "bl", `B${g.batch} ${actors}　${localize(first.batch_label || "")}`));
      for (const e of g.items) {
        let text = localize(e.label || e.summary);
        if (e.proc) text = `[${e.proc}] ${text}`;
        if (e.proxy_by) text = `[proxy by ${e.proxy_by}] ${text}`;
        if (e.continued) text = "… " + text;
        if (e.cont) text += " …";
        const a = manaEl("div", "act" + (e.undone ? " undone" : "") + (e.seq === ui.pos ? " cur" : ""),
          `${e.seq} ${e.actor} ${text}`);
        a.dataset.key = `a${e.seq}`;
        a.title = e.steps.map((s) => "> " + s.op + (s.parent ? ` (${s.parent})` : "") +
          s.events.map((x) => "\n    " + x).join("")).join("\n");
        // Log をクリックしたときは、Log のスクロール位置を動かさない
        a.onclick = () => { onLogSeek(e.seq); };
        d.append(a);
      }
      return d;
    }));
    // 今の位置が変わったとき（と開いたとき）だけ、その行へ寄せる。それ以外はスクロールも選んだ文字もそのまま。
    // Log をクリックして移ったときは寄せない
    const cur = box.querySelector(".act.cur");
    if (ui.keepLogScroll) ui.keepLogScroll = false;
    else if (cur && box.dataset.cur !== String(ui.pos)) box.scrollTop = cur.offsetTop - box.clientHeight / 2;
    box.dataset.cur = ui.pos;
  }


  return { render, renderLog, placeSpeech, showCard, toggleOpen, manaNodes };
}
