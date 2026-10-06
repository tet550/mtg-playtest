// mtgtable 観戦ビューア。サーバーの view / log をそのまま描画する（読み取り専用）。
// 画面上の状態（選んでいる対局・席・再生位置）は画面の側だけで持つ（25節）。

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

const ui = { game: null, seat: "judge", live: true, pos: 0, cursor: 0, view: null, log: [], es: null, names: {},
  images: true, open: new Set(), attacking: new Set(), blocking: new Set(), sides: {}, timer: null };
// 静的サイト（mtgtable export）では、サーバーの API の代わりに data/ の JSON と Scryfall の URL を使う
const STATIC = document.documentElement.dataset.static === "1";
let staticCards = {};
const imageURL = (name) => (STATIC ? (staticCards[name] || {}).image || "data:," : `/api/image?name=${encodeURIComponent(name)}`);
const symbolURL = (code) => (STATIC ? `https://svgs.scryfall.io/card-symbols/${encodeURIComponent(code)}.svg`
  : `/api/symbol?s=${encodeURIComponent(code)}`);
// 静的サイトでは、カードの文を見る人のブラウザが Scryfall から取る（サイトには含めない）
async function scryfallOracle(name) {
  const r = await fetch(`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}`,
    { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(r.statusText);
  const card = await r.json();
  const faces = card.card_faces && card.card_faces.some((f) => f.oracle_text !== undefined) ? card.card_faces : [card];
  // "Treasure" が "Dinosaur // Treasure" に当たるときなどは、名前が一致する面だけ
  const hit = faces.filter((f) => f.name.toLowerCase() === name.toLowerCase());
  return (hit.length ? hit : faces).map((f) => {
    const lines = [`<${f.name}> ${f.mana_cost || ""}`.trim(), f.type_line || card.type_line || ""];
    if (f.oracle_text) lines.push(f.oracle_text);
    const pt = f.power !== undefined ? `${f.power}/${f.toughness}` : (f.loyalty ? `loyalty ${f.loyalty}` : (f.defense ? `defense ${f.defense}` : ""));
    if (pt) lines.push(pt);
    return lines.join("\n");
  }).join("\n\n");
}

const API = {
  games: () => (STATIC ? "data/games.json" : "/api/games"),
  timeline: (g, seat) => (STATIC ? `data/${encodeURIComponent(g)}/timeline.json`
    : `/api/games/${encodeURIComponent(g)}/timeline?seat=${encodeURIComponent(seat)}`),
  log: (g) => (STATIC ? `data/${encodeURIComponent(g)}/log.json` : `/api/games/${encodeURIComponent(g)}/log`),
};

// 文の中の {G} {2} {T} {W/U} などをマナ・シンボルの画像にした要素の並び（画像を使わないときは文字のまま）
function manaNodes(text) {
  if (!ui.images) return [document.createTextNode(text)];
  const out = [];
  let last = 0;
  for (const m of text.matchAll(/\{([^{}]{1,5})\}/g)) {
    if (m.index > last) out.push(document.createTextNode(text.slice(last, m.index)));
    const img = el("img", "ms");
    img.alt = m[0];
    img.title = m[0];
    img.src = symbolURL(m[1].replace("/", ""));
    img.onerror = () => img.replaceWith(document.createTextNode(m[0]));
    out.push(img);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(document.createTextNode(text.slice(last)));
  return out;
}
const manaEl = (tag, cls, text) => { const e = el(tag, cls); e.append(...manaNodes(text)); return e; };
const oracleCache = new Map();

async function getJSON(url) {
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

function remember(key, value) {
  try { localStorage.setItem("mtgtable." + key, value); } catch (_) { /* 保存できなくても動く */ }
}
function recall(key) {
  try { return localStorage.getItem("mtgtable." + key); } catch (_) { return null; }
}

// ---------------------------------------------------------------- 読み込み

async function loadGames() {
  const games = await getJSON(API.games());
  const sel = $("game");
  sel.replaceChildren(...games.map((g) => {
    const o = el("option", null, `${g.id}（T${g.turn}・v${g.version}）`);
    o.value = g.id;
    return o;
  }));
  const saved = recall("game");
  ui.game = games.some((g) => g.id === saved) ? saved : (games[0] && games[0].id);
  if (ui.game) sel.value = ui.game;
}

// 対局（と席）の時系列と Log をまとめて取る。再生はこの時系列から手元で行い、通信しない
async function load() {
  if (!ui.game) return;
  const tl = await getJSON(API.timeline(ui.game, ui.seat));
  ui.log = ui.seat === "judge" ? await getJSON(API.log(ui.game)) : [];
  ui.timeline = tl;
  ui.cursor = tl.cursor;
  ui.views = new Map();
  show();
}

// i 件目の後の view を、近い丸ごとの view（または作った view）から差分を当てて作る
function viewAt(pos) {
  if (ui.views.has(pos)) return ui.views.get(pos);
  const frames = ui.timeline.frames;
  let base = pos;
  while (!ui.views.has(base) && !frames[base].k) base--;
  const v = structuredClone(ui.views.has(base) ? ui.views.get(base) : frames[base].k);
  for (let i = base + 1; i <= pos; i++) {
    for (const op of frames[i].d) {
      const path = op[1];
      if (!path.length) { Object.assign(v, structuredClone(op[2])); continue; }
      let o = v;
      for (const k of path.slice(0, -1)) o = o[k];
      if (op[0] === "s") o[path[path.length - 1]] = structuredClone(op[2]);
      else delete o[path[path.length - 1]];
    }
  }
  ui.views.set(pos, v);
  if (ui.views.size > 64) ui.views.delete(ui.views.keys().next().value);
  return v;
}

function show() {
  if (!ui.timeline) return;
  if (ui.live) ui.pos = ui.cursor;
  ui.pos = Math.max(0, Math.min(ui.pos, ui.cursor));
  ui.view = viewAt(ui.pos);
  render();
}

function listen() {
  if (ui.es) ui.es.close();
  if (!ui.game || STATIC) return;  // 静的サイトは更新されない
  ui.es = new EventSource(`/api/games/${encodeURIComponent(ui.game)}/events`);
  ui.es.onopen = () => $("conn").classList.add("ok");
  ui.es.onerror = () => $("conn").classList.remove("ok");
  ui.es.onmessage = (ev) => {
    // 対局が更新されたら（AI が書いた・Undo した）時系列を取り直す
    const msg = JSON.parse(ev.data);
    const last = ui.timeline && viewAt(ui.cursor);
    if (!last || msg.cursor !== ui.cursor || msg.version !== last.version) load().catch(showError);
  };
}

function showError(e) {
  $("detail").textContent = "エラー: " + e.message;
}

// ---------------------------------------------------------------- 描画

function collectNames(v) {
  const names = {};
  for (const z of Object.values(v.zones)) {
    for (const key of ["cards", "known", "known_positions", "known_unordered"]) {
      for (const c of z[key] || []) if (c.name) names[c.id] = c.name;
    }
  }
  return names;
}

const ref = (id) => (ui.names[id] ? `<${ui.names[id]}> ${id}` : id);

// 同じ見た目のパーマネント（Pizza のコピー ×100 など）は1枚にまとめて ×N で出す
function groupCards(cards) {
  const groups = [];
  for (const c of cards) {
    const { id, ...rest } = c;
    const key = JSON.stringify(rest);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.ids.push(id);
    else groups.push({ key, card: c, ids: [id] });
  }
  return groups.map((g) => (g.ids.length === 1 ? g.card
    : { ...g.card, id: `${g.ids[0]}..${g.ids[g.ids.length - 1]}`, count: g.ids.length }));
}

// 画像の上に、エンジンが持っている情報（カウンター・×N）を重ねる。画像が無ければ名前の文字だけ
function art(c) {
  const box = el("div", "artbox");
  if (c.face_down || !c.name) {
    box.classList.add("back");
  } else {
    const img = el("img", "art");
    img.loading = "lazy";
    img.alt = c.name;
    img.src = imageURL(c.name);
    img.onerror = () => { box.classList.add("noimg"); img.remove(); };
    box.append(img);
  }
  const flags = el("div", "flags");
  if (c.token) flags.append(el("span", "chip strong", "token"));
  if (c.new) flags.append(el("span", "chip strong", "new"));
  if (ui.attacking.has(c.id)) flags.append(el("span", "chip strong atk", "攻撃"));
  if (ui.blocking.has(c.id)) flags.append(el("span", "chip strong blk", "ブロック"));
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
  return box;
}

// Note と Link を、カードの上に出す短い文に（until は「まで」を付ける。Link の id は名前に）
function cardTexts(c) {
  const out = (c.notes || []).map((n) => n.text + (n.until ? `（${n.until.replace(/_/g, " ")}）` : ""));
  for (const l of c.links || []) {
    const m = /^(\w+)(->|<-)(.*)$/.exec(l);
    if (!m) { out.push(l); continue; }
    if (ui.images && m[1] === "exiled_by") continue;
    const ids = m[3].split(",").map((id) => (ui.names[id] ? `<${ui.names[id]}>` : id));
    out.push(`${m[2] === "->" ? "→" : "←"} ${ids.join(", ")}（${m[1].replace(/_/g, " ")}）`);
  }
  return out;
}

function cardTile(c, extra) {
  const small = (extra || "").includes("small");
  const withArt = ui.images && !small;
  const d = el("div", "card" + (c.tapped ? " tapped" : "") + (c.land ? " land" : "") + (withArt ? " withart" : "") + (extra || ""));
  d.title = (c.name ? `<${c.name}>` : "（裏向き）") + ` ${c.id}`;
  d.onclick = () => showCard(c);
  if (withArt) {
    d.append(art(c));
    return d;
  }
  d.append(el("div", "nm", (c.name ? `<${c.name}>` : (c.face_down ? "（裏向き）" : "?")) + (c.count ? ` ×${c.count}` : "")));
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

async function showCard(c) {
  document.querySelectorAll(".card.sel").forEach((x) => x.classList.remove("sel"));
  const lines = [c.name ? `<${c.name}>  ${c.id}` : c.id];
  const state = [c.tapped && "tapped", c.token && "token", c.new && "new", c.face_down && "face down",
    c.controller && c.owner && c.controller !== c.owner && `owner ${c.owner}`].filter(Boolean);
  if (state.length) lines.push(state.join(" · "));
  for (const [k, n] of Object.entries(c.counters || {})) lines.push(`counter ${k} ×${n}`);
  for (const t of cardTexts(c)) lines.push("note: " + t);
  if (c.definition) lines.push("definition: " + JSON.stringify(c.definition));
  if (c.name) {
    if (!oracleCache.has(c.name)) {
      const r = STATIC ? { text: await scryfallOracle(c.name).catch(() => "") }
        : await getJSON(`/api/oracle?name=${encodeURIComponent(c.name)}`).catch(() => ({}));
      oracleCache.set(c.name, r.text || "（オラクルを取得できなかった）");
    }
    lines.push("", oracleCache.get(c.name));
  }
  const detail = $("detail");
  detail.replaceChildren();
  if (ui.images && c.name && !c.face_down) {
    const img = el("img", "big");
    img.alt = c.name;
    img.src = imageURL(c.name);
    img.onerror = () => img.remove();
    detail.append(img);
  }
  detail.append(manaEl("div", "otext", lines.join("\n")));
  detail.classList.remove("muted");
}

function zoneBlock(title, nodes) {
  const z = el("div", "zone");
  z.append(el("h3", null, title));
  const row = el("div", "row");
  row.append(...nodes);
  z.append(row);
  return z;
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
    const cards = g.key === "tapped" ? [...g.cards].sort((a, b) => a.name.localeCompare(b.name)) : g.cards;
    box.append(...cards.map((c) => withUnder(c, under)));
    if (g.cards.length > 1) box.append(el("span", "chip strong gcount", `×${g.cards.length}`));
    return box;
  });
}

// あるカードによって追放されたカード（exiled_by の Link）を、そのカードの下に差し込んだように出す
function exiledUnder(v) {
  const exile = new Map((v.zones.exile.cards || []).map((c) => [c.id, c]));
  const under = {};
  for (const l of v.links) {
    if (l.kind !== "exiled_by") continue;
    for (const t of l.targets) {
      if (exile.has(t)) (under[l.source] = under[l.source] || []).push(exile.get(t));
    }
  }
  return under;
}

function withUnder(c, under) {
  const tile = cardTile(c);
  const cards = ui.images ? under[c.id] || [] : [];
  if (!cards.length) return tile;
  const box = el("div", "tuck");
  box.style.setProperty("--n", cards.length);
  cards.forEach((u, i) => {
    const t = cardTile(u, " under");
    t.style.top = `calc(${i} * var(--h, 151px) * var(--peek, .12))`;
    box.append(t);
  });
  box.append(tile);
  return box;
}

// ライブラリー・墓地・追放をカードの束で表す。墓地と追放は一番上のカードを表に、クリックで中身を開く
function pile(label, count, top, opts) {
  const p = el("div", "pile" + (count ? "" : " empty") + (opts.open ? " open" : ""));
  const stackBox = el("div", "pstack");
  for (let i = Math.min(count, 4) - 1; i >= 1; i--) {
    const layer = el("div", "layer");
    layer.style.transform = `translate(${i * 2}px, ${i * 2}px)`;
    stackBox.append(layer);
  }
  const face = el("div", "face" + (top ? "" : " back"));
  if (top && ui.images) {
    const img = el("img");
    img.alt = top.name;
    img.src = imageURL(top.name);
    img.onerror = () => { img.remove(); face.append(el("div", "ftext", `<${top.name}>`)); };
    face.append(img);
  } else if (top) {
    face.append(el("div", "ftext", `<${top.name}>`));
  }
  if (count) stackBox.append(face);
  stackBox.append(el("span", "chip strong pcount", String(count)));
  stackBox.append(el("div", "plabel", label));  // 名前はカードの上に重ねる（縦の幅を取らない）
  p.append(stackBox);
  if (opts.note) p.title = (p.title ? p.title + "\n" : "") + opts.note;
  if (opts.onclick && count) p.onclick = opts.onclick;
  if (top) p.title = `一番上: <${top.name}>`;
  return p;
}

function toggleOpen(key) {
  if (ui.open.has(key)) ui.open.delete(key); else ui.open.add(key);
  render();
}

// 開いた束の中身。画像で並べ、ライブラリーは上からの位置を重ねて出す
function openedBlock(title, cards) {
  const z = el("div", "zone opened");
  z.append(el("h3", null, title));
  const row = el("div", "row");
  row.append(...(cards.length ? cards.map((c) => cardTile(c, ui.images ? " inhand" : " small"))
    : [el("div", "muted", "知っているカードはない")]));
  z.append(row);
  return z;
}

function libraryCards(lib) {
  const known = (lib.known_positions || []).map((c) => ({ ...c, pos: `上から ${c.index + 1}` }));
  const unordered = (lib.known_unordered || []).map((c) => ({ ...c, pos: "位置不明" }));
  return [...known, ...unordered];
}

function handRow(v, pid) {
  const hand = v.zones[`${pid}.hand`];
  const shown = hand.cards || hand.known || [];
  const row = el("div", "hand" + (ui.images ? " fan" : ""));
  row.append(...shown.map((c) => cardTile(c, " inhand")));
  for (let i = shown.length; i < hand.count; i++) row.append(el("div", "card back inhand"));
  const box = el("div", "handbox");
  box.append(el("h3", null, `手札 ${hand.count}`), row);
  return box;
}

// ライフ: 「LIFE」と数のプレート。5 以下は警告色、0 以下は敗北の色
// 直前の位置（1件前）からライフが変わっていれば、その差を横に出す（減ったら赤、増えたら緑）
function lifeBadge(p, before) {
  const level = p.life <= 0 ? " dead" : p.life <= 5 ? " low" : "";
  const box = el("span", "life" + level);
  box.title = `ライフ ${p.life}`;
  box.append(el("span", "llbl", "LIFE"), el("span", "lnum", String(p.life)));
  const delta = before === undefined ? 0 : p.life - before;
  if (!delta) return box;
  const wrap = el("span", "lifewrap");
  const d = el("span", "ldelta " + (delta < 0 ? "minus" : "plus"), delta < 0 ? `−${-delta}` : `+${delta}`);
  d.title = `${before} → ${p.life}`;
  wrap.append(box, d);
  return wrap;
}

function playerSide(v, pid, mirrored, prev) {
  const p = v.players.find((x) => x.id === pid);
  const box = el("div");
  const head = el("div", "phead");
  const before = prev && prev.players.find((x) => x.id === pid);
  head.append(el("span", "pname", `${p.name}（${p.id}）`), lifeBadge(p, before && before.life));
  if (p.status && p.status !== "playing") head.append(el("span", "status", p.status));
  if (v.turn.active === pid) head.append(el("span", "chip", "アクティブ"));
  if (v.turn.priority === pid) head.append(el("span", "chip", "優先権"));
  for (const [k, n] of Object.entries(p.counters || {})) head.append(el("span", "chip", `${k} ×${n}`));
  for (const m of p.mana) {
    const chip = el("span", "chip mana");
    chip.append(...manaNodes(`{${m.color}}`), document.createTextNode(` ×${m.amount}`));
    if (m.notes) {
      const t = m.notes.map((n) => n.text).join("; ");
      chip.append(document.createTextNode(`（${t.length > 40 ? t.slice(0, 38) + "…" : t}）`));
      chip.title = t;
    }
    head.append(chip);
  }
  for (const n of p.notes || []) head.append(el("span", "chip", n.text));

  // 束: ライブラリー・墓地・追放
  const piles = el("div", "piles");
  const lib = v.zones[`${pid}.library`];
  const libKey = `${pid}.library`;
  piles.append(pile("ライブラリー", lib.count, null,
    { note: (lib.known_positions || []).length ? `位置既知 ${lib.known_positions.length}` : "",
      open: ui.open.has(libKey), onclick: () => toggleOpen(libKey) }));
  const gy = v.zones[`${pid}.graveyard`];
  const gyKey = `${pid}.graveyard`;
  piles.append(pile("墓地", gy.count, (gy.cards || [])[0], { open: ui.open.has(gyKey), onclick: () => toggleOpen(gyKey) }));
  const ex = (v.zones.exile.cards || []).filter((c) => c.owner === pid);
  const exKey = `${pid}.exile`;
  if (ex.length) piles.append(pile("追放", ex.length, ex[ex.length - 1], { open: ui.open.has(exKey), onclick: () => toggleOpen(exKey) }));

  // 戦場: 土地以外と土地（土地は名前ごとに束ねる）
  const bf = (v.zones.battlefield.cards || []).filter((c) => (c.controller || c.owner) === pid);
  const under = exiledUnder(v);
  const others = el("div", "row bfrow"); others.append(...groupCards(bf.filter((c) => !c.land)).map((c) => withUnder(c, under)));
  const lands = el("div", "row bfrow lands"); lands.append(...landGroups(bf.filter((c) => c.land), under));
  const field = el("div", "field");
  field.append(...(mirrored ? [lands, others] : [others, lands]));

  const grid = el("div", "sidegrid");
  grid.append(piles, field);

  // 開いた墓地・追放の中身
  const opened = [];
  if (ui.open.has(libKey) && !lib.collapsed) {
    opened.push(openedBlock(`ライブラリー ${lib.count}（知っているカード。上から）`, libraryCards(lib)));
  }
  if (ui.open.has(gyKey) && gy.count) {
    opened.push(openedBlock(`墓地 ${gy.count}（上から）`, (gy.cards || []).map((c, i) => ({ ...c, pos: String(i + 1) }))));
  }
  if (ui.open.has(exKey) && ex.length) opened.push(openedBlock(`追放 ${ex.length}`, ex));

  const hand = handRow(v, pid);
  box.append(...(mirrored ? [head, hand, grid, ...opened] : [head, grid, ...opened, hand]));
  return box;
}

// スタック: 上から順に、呪文はそのカード、能力は発生源のカードを小さく出し、横に種類・文・対象を並べる
const KIND = { spell: "呪文", activated: "起動型能力", triggered: "誘発型能力", ability: "能力" };

function renderStack(v) {
  const box = $("stack");
  if (!v.stack.length) {
    box.replaceChildren(el("li", "muted none", "空"));
    return;
  }
  const cards = new Map();
  for (const z of Object.values(v.zones)) for (const c of z.cards || []) cards.set(c.id, c);
  box.replaceChildren(...v.stack.map((s, i) => {
    const li = el("li", "sitem" + (s.kind === "spell" ? "" : " ability"));
    const id = s.card || s.source;
    const c = cards.get(id) || (id && ui.names[id] ? { id, name: ui.names[id] } : null);
    const pic = c ? cardTile({ ...c, tapped: false, counters: {}, notes: [], links: [] }, ui.images ? " instack" : " small")
      : el("div", "card small", "?");
    if (s.kind !== "spell") pic.classList.add("src");
    const body = el("div", "sbody");
    const head = el("div", "shead");
    head.append(el("span", "chip" + (i === 0 ? " strong" : ""), i === 0 ? "一番上" : String(i + 1)),
      el("strong", null, ` ${KIND[s.kind] || s.kind}`), document.createTextNode(`　${s.controller}`));
    body.append(head);
    if (c && c.name) body.append(el("div", "sname", (s.kind === "spell" ? "" : "源: ") + `<${c.name}>`));
    if (s.text) {
      const t = manaEl("div", "stext", s.text);
      t.title = s.text;
      body.append(t);
    }
    const targets = v.links.filter((l) => l.source === s.id && l.kind === "target").flatMap((l) => l.targets);
    if (targets.length) {
      const tg = el("div", "stargets");
      tg.append(el("span", "muted", "対象 "), ...targets.map((t) => el("span", "chip", ui.names[t] ? `<${ui.names[t]}>` : t)));
      body.append(tg);
    }
    li.append(pic, body);
    li.title = s.id;
    return li;
  }));
}

// 戦闘: 攻撃クリーチャーごとに、カード・攻撃先・ブロックしているカードを縦に並べる
function renderCombat(v) {
  const box = $("combat");
  $("combatbox").hidden = !v.combat.attacks.length;
  if (!v.combat.attacks.length) {
    box.replaceChildren();
    return;
  }
  const cards = new Map((v.zones.battlefield.cards || []).map((c) => [c.id, c]));
  const tile = (id) => (cards.has(id) ? cardTile(cards.get(id), ui.images ? " incombat" : " small")
    : el("div", "card small", ref(id)));
  box.replaceChildren(...v.combat.attacks.map((a) => {
    const col = el("div", "cbcol");
    // 攻撃先は、画面の上下のどちらの Player かを矢印で（Player 以外はカード名）
    const arrow = a.target === ui.sides.top ? "↑" : a.target === ui.sides.bottom ? "↓" : "→";
    const to = el("div", "cbto");
    to.append(el("span", "arw", arrow), document.createTextNode(cards.has(a.target) || ui.names[a.target]
      ? ` <${ui.names[a.target] || a.target}>` : ` ${a.target}`));
    to.title = `攻撃先: ${ref(a.target)}`;
    // ブロック側は防御側の Player の位置に合わせる（上の Player を攻撃するなら、ブロックは攻撃カードの上）
    const defense = [];
    const blockers = v.combat.blocks.filter((b) => b.attacker === a.attacker);
    if (blockers.length) {
      const row = el("div", "cbrow");
      row.append(...blockers.map((b) => tile(b.blocker)));
      defense.push(el("div", "cbblk", "ブロック"), row);
    } else if (["declare_blockers", "combat_damage", "first_strike_damage", "end_of_combat"].includes(v.turn.step)) {
      defense.push(el("div", "cbblk muted", "ブロックなし"));
    }
    if (arrow === "↑") col.append(...defense.reverse(), to, tile(a.attacker));
    else col.append(tile(a.attacker), to, ...defense);
    return col;
  }));
}

function render() {
  const v = ui.view;
  ui.names = collectNames(v);
  ui.attacking = new Set(v.combat.attacks.map((a) => a.attacker));
  ui.blocking = new Set(v.combat.blocks.map((b) => b.blocker));
  const t = v.turn;
  $("turn").textContent = t.turn === 0
    ? `ゲーム前（先攻 ${t.active}）`
    : `T${t.turn} ${t.phase}/${t.step}　アクティブ ${t.active}　優先権 ${t.priority || "-"}`;

  // 視点の Player を下に。judge は先攻を上に
  const order = v.players.map((p) => p.id);
  const me = v.viewer && order.includes(v.viewer) ? v.viewer : order[order.length - 1];
  const opp = order.find((p) => p !== me) || me;
  ui.sides = { top: opp, bottom: me };
  const from = ui.deltaBase && ui.deltaBase.pos === ui.pos ? ui.deltaBase.from : ui.pos - 1;
  for (const [slot, pid] of [["top", opp], ["bottom", me]]) {
    const side = $(slot);
    side.replaceChildren(playerSide(v, pid, slot === "top", from >= 0 && from < ui.pos ? viewAt(from) : null));
    side.classList.toggle("active", t.active === pid);
  }

  renderStack(v);

  renderCombat(v);
  const decl = v.declarations.slice(-3).reverse().map((d) => el("li", null, `${d.player} ${d.kind}${d.text ? "：" + d.text : ""}`));
  $("decl").replaceChildren(...(decl.length ? decl : [el("li", "muted", "なし")]));

  const pos = $("pos");
  pos.max = ui.cursor;
  pos.value = ui.pos;
  $("posText").textContent = `${ui.pos} / ${ui.cursor} 件目の後（v${v.version}）`;
  $("live").classList.toggle("on", ui.live);
  renderLog();
}

function renderLog() {
  const box = $("log");
  const prevScroll = box.scrollTop;
  if (ui.seat !== "judge") {
    box.replaceChildren(el("div", "batch muted", "Log は judge の席だけ（AI のラベルに非公開の情報が入りうるため）"));
    return;
  }
  const groups = [];
  for (const e of ui.log) {
    const last = groups[groups.length - 1];
    if (last && last.batch === e.batch) last.items.push(e);
    else groups.push({ batch: e.batch, items: [e] });
  }
  box.replaceChildren(...groups.map((g) => {
    const d = el("div", "batch");
    const first = g.items[0];
    const actors = [...new Set(g.items.map((e) => e.actor))].join(",");
    d.append(manaEl("div", "bl", `B${g.batch} ${actors}　${first.batch_label || ""}`));
    for (const e of g.items) {
      let text = e.label || e.summary;
      if (e.proc) text = `[${e.proc}] ${text}`;
      if (e.proxy_by) text = `[proxy by ${e.proxy_by}] ${text}`;
      if (e.continued) text = "… " + text;
      if (e.cont) text += " …";
      const a = manaEl("div", "act" + (e.undone ? " undone" : "") + (e.seq === ui.pos ? " cur" : ""),
        `${e.seq} ${e.actor} ${text}`);
      a.title = e.steps.map((s) => "> " + s.op + (s.parent ? ` (${s.parent})` : "") +
        s.events.map((x) => "\n    " + x).join("")).join("\n");
      // Log をクリックしたときは、Log のスクロール位置を動かさない
      a.onclick = () => { stopPlay(); ui.keepLogScroll = true; ui.live = false; ui.pos = Math.min(e.seq, ui.cursor); show(); };
      d.append(a);
    }
    return d;
  }));
  const cur = box.querySelector(".act.cur");
  if (ui.keepLogScroll) {
    box.scrollTop = prevScroll;
    ui.keepLogScroll = false;
  } else if (cur) {
    box.scrollTop = cur.offsetTop - box.clientHeight / 2;
  }
}

// ---------------------------------------------------------------- 操作

function seek(pos) {
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
  $("play").textContent = "▶ 再生";
  $("play").classList.remove("on");
}

function startPlay() {
  if (ui.pos >= ui.cursor) ui.pos = 0;  // 最後まで見ていたら最初から
  ui.live = false;
  $("play").textContent = "⏸ 停止";
  $("play").classList.add("on");
  const step = async () => {
    if (ui.pos >= ui.cursor) {  // 最後まで来たら止めて Live に戻る
      stopPlay();
      ui.live = true;
      return show();
    }
    const from = ui.pos;
    ui.pos = Math.min(nextPos(), ui.cursor);
    ui.deltaBase = { pos: ui.pos, from };  // 自動再生では、ライフの差を1回前に表示した位置から数える
    ui.live = false;
    show();
    if (ui.timer !== null) ui.timer = setTimeout(step, Number($("speed").value));
  };
  ui.timer = setTimeout(step, 0);
}

const manual = (f) => (...args) => { stopPlay(); return f(...args); };

$("game").onchange = (e) => { stopPlay(); ui.open.clear(); ui.game = e.target.value; remember("game", ui.game); ui.live = true; load().then(listen).catch(showError); };
$("images").onchange = (e) => { ui.images = e.target.checked; remember("images", ui.images ? "1" : "0"); render(); };
$("seat").onchange = (e) => { ui.seat = e.target.value; remember("seat", ui.seat); load().catch(showError); };
$("pos").oninput = manual((e) => seek(Number(e.target.value)));
$("first").onclick = manual(() => seek(0));
$("prev").onclick = manual(() => seek(ui.pos - 1));
$("next").onclick = manual(() => seek(ui.pos + 1));
$("live").onclick = manual(() => { ui.live = true; show(); });
$("play").onclick = () => (ui.timer !== null && ui.timer !== undefined ? stopPlay() : startPlay());
$("unit").onchange = (e) => remember("unit", e.target.value);
$("speed").onchange = (e) => remember("speed", e.target.value);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "ArrowLeft") manual(() => seek(ui.pos - 1))();
  if (e.key === "ArrowRight") manual(() => seek(ui.pos + 1))();
  if (e.key === " ") { e.preventDefault(); $("play").click(); }
});

(async () => {
  try {
    if (STATIC) {
      staticCards = await getJSON("data/cards.json");
      $("seat").replaceChildren(...[...$("seat").options].filter((o) => o.value === "judge"));
      $("conn").hidden = true;
      remember("seat", "judge");
    }
    ui.seat = recall("seat") || "judge";
    $("seat").value = ui.seat;
    ui.images = recall("images") !== "0";
    $("images").checked = ui.images;
    if (recall("unit")) $("unit").value = recall("unit");
    if (recall("speed")) $("speed").value = recall("speed");
    await loadGames();
    await load();
    listen();
  } catch (e) { showError(e); }
})();
