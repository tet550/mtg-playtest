import { $, el } from "./dom.js";
import { createMotion } from "./motion.js";

// DOM 描画。通信と再生操作は呼び出し元から渡す。
export function createRenderer(ui, { source, viewAt, onLogSeek, clampFloats }) {
  const { imageURL, symbolURL } = source;
  const motion = createMotion();
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
      : { ...g.card, id: `${g.ids[0]}..${g.ids[g.ids.length - 1]}`, count: g.ids.length, ids: g.ids }));
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
    d.dataset.ids = (c.ids || [c.id]).join(" ");  // カードの動き（motion.js）で前後の描画を対応づける
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

  let detailRequest = 0;
  async function showCard(c) {
    const request = ++detailRequest;
    const view = ui.view;
    document.querySelectorAll(".card.sel").forEach((x) => x.classList.remove("sel"));
    const lines = [c.name ? `<${c.name}>  ${c.id}` : c.id];
    const state = [c.tapped && "tapped", c.token && "token", c.new && "new", c.face_down && "face down",
      c.controller && c.owner && c.controller !== c.owner && `owner ${c.owner}`].filter(Boolean);
    if (state.length) lines.push(state.join(" · "));
    for (const [k, n] of Object.entries(c.counters || {})) lines.push(`counter ${k} ×${n}`);
    for (const t of cardTexts(c)) lines.push("note: " + t);
    if (c.definition) lines.push("definition: " + JSON.stringify(c.definition));
    if (c.name) {
      lines.push("", await source.oracle(c.name));
    }
    if (request !== detailRequest || view !== ui.view) return;
    const detail = $("detail");
    $("fl-detail").hidden = false;
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
    p.dataset.zone = opts.zone;
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
    box.dataset.zone = `${pid}.hand`;
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
    // 増減の欄はいつも取っておく（出た時に横の並びがずれないように）
    const wrap = el("span", "lifewrap");
    wrap.append(box);
    const delta = before === undefined ? 0 : p.life - before;
    if (!delta) return wrap;
    const d = el("span", "ldelta " + (delta < 0 ? "minus" : "plus"), delta < 0 ? `−${-delta}` : `+${delta}`);
    d.title = `${before} → ${p.life}`;
    wrap.append(d);
    return wrap;
  }

  // 宣言は、その Player の名前の横に吹き出しで出す。今のターン・フェイズ・ステップでの最後の宣言だけ。
  // パスは優先権が続けてパスされている間だけ（スタックが変わる・ステップが進むと消える。turn.passed と同じ）
  const DECL = { pass: "パス", keep: "キープ", mulligan: "マリガン", no_block: "ブロックなし", concede: "投了" };
  // 出現のアニメーションは、その位置で新しく出た宣言だけ（続いている宣言を描き直すたびに動かさない）
  function speech(v, pid, prev) {
    const t = v.turn;
    const d = v.declarations.filter((x) => x.player === pid && x.turn === t.turn && x.step === t.step
      && (!x.phase || x.phase === t.phase)).pop();
    if (!d || (d.kind === "pass" && !t.passed.includes(pid))) return null;
    const word = DECL[d.kind] || d.kind;
    const text = d.text && !d.text.startsWith("standing") ? `${word}：${d.text}` : word;
    const fresh = !prev || !prev.declarations.some((x) => x.seq === d.seq);
    const b = el("span", "speech" + (fresh ? " fresh" : ""));
    b.append(el("span", "stx", text));
    b.title = `${d.player} ${d.kind}${d.text ? "：" + d.text : ""}`;
    return b;
  }

  function playerSide(v, pid, mirrored, prev) {
    const p = v.players.find((x) => x.id === pid);
    const box = el("div");
    const head = el("div", "phead");
    const before = prev && prev.players.find((x) => x.id === pid);
    head.append(el("span", "pname", `${p.name}（${p.id}）`), lifeBadge(p, before && before.life));
    // アクティブ Player のライフの横に、今のフェイズ
    if (v.turn.active === pid && v.turn.turn > 0) head.append(phaseStrip(v.turn));
    // 優先権の欄はいつも取っておく（持ち主が替わっても横の並びがずれないように）
    head.append(el("span", "chip prio" + (v.turn.priority === pid ? "" : " off"), "優先権"));
    // 残り（状態・マナ・カウンターなど）は右の余白の中だけに並べ、入らない分は切る（左の並びを押さない）
    const extra = el("span", "pextra");
    head.append(extra);
    if (p.status && p.status !== "playing") extra.append(el("span", "status", p.status));
    if (v.turn.active === pid && v.turn.turn === 0) extra.append(el("span", "chip", "先攻"));
    for (const [k, n] of Object.entries(p.counters || {})) extra.append(el("span", "chip", `${k} ×${n}`));
    for (const m of p.mana) {
      const chip = el("span", "chip mana");
      chip.append(...manaNodes(`{${m.color}}`), document.createTextNode(` ×${m.amount}`));
      if (m.notes) {
        const t = m.notes.map((n) => n.text).join("; ");
        chip.append(document.createTextNode(`（${t.length > 40 ? t.slice(0, 38) + "…" : t}）`));
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
    piles.append(pile("ライブラリー", lib.count, null,
      { note: (lib.known_positions || []).length ? `位置既知 ${lib.known_positions.length}` : "",
        open: ui.open.has(libKey), onclick: () => toggleOpen(libKey), zone: libKey }));
    const gy = v.zones[`${pid}.graveyard`];
    const gyKey = `${pid}.graveyard`;
    piles.append(pile("墓地", gy.count, (gy.cards || [])[0], { open: ui.open.has(gyKey), onclick: () => toggleOpen(gyKey), zone: gyKey }));
    const ex = (v.zones.exile.cards || []).filter((c) => c.owner === pid);
    const exKey = `${pid}.exile`;
    if (ex.length) piles.append(pile("追放", ex.length, ex[ex.length - 1], { open: ui.open.has(exKey), onclick: () => toggleOpen(exKey), zone: exKey }));

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
    // 宣言の吹き出しは見出しから戦場の側へはみ出す（位置は placeSpeech で合わせる）
    const said = speech(v, pid, prev);
    if (said) box.append(said);
    return box;
  }

  // 吹き出しをライフの真下に置く（名前の長さで変わるので、描いた後に測る）
  function placeSpeech() {
    for (const b of document.querySelectorAll(".speech")) {
      const head = b.parentElement.querySelector(".phead");
      const life = head && head.querySelector(".life");
      if (!life) continue;
      b.style.left = `${life.getBoundingClientRect().left - b.parentElement.getBoundingClientRect().left + 4}px`;
      b.style.top = `${head.offsetTop + head.offsetHeight + 6}px`;
    }
  }

  // スタック: 上から順に、呪文はそのカード、能力は発生源のカードを小さく出し、横に種類・文・対象を並べる
  const KIND = { spell: "呪文", activated: "起動型能力", triggered: "誘発型能力", ability: "能力" };

  function renderStack(v) {
    const box = $("stack");
    $("fl-stack").hidden = !v.stack.length;
    if (!v.stack.length) {
      box.replaceChildren();
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
  const PHASES = [["beginning", "開始"], ["main1", "メイン1"], ["combat", "戦闘"], ["main2", "メイン2"], ["ending", "終了"]];
  const STEP_JA = {
    untap: "アンタップ", upkeep: "アップキープ", draw: "ドロー", main: "メイン", beginning_of_combat: "戦闘開始",
    declare_attackers: "攻撃", declare_blockers: "ブロック", combat_damage: "ダメージ", first_strike_damage: "先制ダメージ",
    end_of_combat: "戦闘終了", end: "終了ステップ", cleanup: "クリンナップ",
  };

  function icon(d) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
    return svg;
  }

  function phaseStrip(t) {
    const strip = el("span", "phases");
    strip.title = `${t.phase}/${t.step}`;
    for (const [ph, name] of PHASES) {
      const cur = ph === t.phase;
      const p = el("span", "ph" + (cur ? " cur" : ""));
      p.title = name;
      p.append(icon(PHASE_ICON[ph]));
      if (cur) p.append(el("span", "pstep", STEP_JA[t.step] || t.step));
      strip.append(p);
    }
    return strip;
  }

  function render() {
    const v = ui.view;
    if (!v) return;
    // カードの動きは、呼び出し元が ui.animate を立てた描画（1件ずつの移動・自動再生）だけ
    const animate = ui.animate && ui.motion ? ui.animate : null;
    ui.animate = null;
    motion.stop();
    const before = animate && ui.shown ? motion.capture() : null;
    const shown = ui.shown;
    ui.names = collectNames(v);
    ui.attacking = new Set(v.combat.attacks.map((a) => a.attacker));
    ui.blocking = new Set(v.combat.blocks.map((b) => b.blocker));
    const t = v.turn;
    $("turn").textContent = t.turn === 0 ? "ゲーム前" : `T${t.turn}`;

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

    const pos = $("pos");
    pos.max = ui.cursor;
    pos.value = ui.pos;
    $("posText").textContent = `${ui.pos} / ${ui.cursor} 件目の後（v${v.version}）`;
    $("live").classList.toggle("on", ui.live);
    renderLog();
    placeSpeech();
    clampFloats();
    ui.shown = v;
    if (before && shown !== v) motion.play(before, shown, v, animate.duration);
  }

  function renderLog() {
    if (!document.body.classList.contains("log-open")) return;  // 閉じているときは作らない
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
        a.onclick = () => { onLogSeek(e.seq); };
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


  return { render, renderLog, placeSpeech };
}
