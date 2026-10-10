// GUI の対局: 席の Player として審判に依頼する（request_play_design）。人間も AI も卓（盤面）は動かさない。
// 土地を出す・唱える・攻撃する・1 枚引く…は、ブラウザの中の下書き（依頼の行）に足すだけで、送るまで盤面は変わらない。
// 「審判に依頼」で下書きを1つの依頼として審判に送り（「その後」は下書きの最後の行で決まる）、審判がルールに沿って卓に書く。
// 引く・見るなど隠れた情報の行は、手札・束に「？」の予定を出すだけ（中身は審判の処理の後に、席の view として届く）。
import { $, dots, el } from "./dom.js";
import { t as tr } from "./i18n.js";  // t はターンなどの変数名に使っているので tr
import { displayName, localize } from "./cardnames.js";

// ---------------------------------------------------------------- 純粋な部品（node のテストでも使う）

export const STEPS = ["untap", "upkeep", "draw", "main1", "beginning_of_combat", "declare_attackers",
  "declare_blockers", "combat_damage", "end_of_combat", "main2", "end", "cleanup"];
// ステップの名前（画面の言語で）
export const stepLabel = (s) => (STEPS.includes(s) ? tr(`play.step.${s}`) : s);

// 止める場所の列（play.py の STOP_STEPS と同じ順）。見出しはフェイズごとにまとめる
export const STOP_STEPS = ["upkeep", "draw", "main1", "beginning_of_combat", "declare_blockers", "combat_damage",
  "end_of_combat", "main2", "end"];
// 列の短い名前は辞書の play.stopShort.<ステップ>、フェイズの見出しは play.group.<フェイズ>
const STOP_GROUPS = [{ group: "beginning", n: 2 }, { group: "main1", n: 1 }, { group: "combat", n: 4 }, { group: "main2", n: 1 }, { group: "ending", n: 1 }];
export const STOP_PRESETS = [  // 名前は辞書の play.preset.<名前>
  ["recommended", ["own:main1", "own:main2", "opp:declare_blockers", "opp:end", "opp:spell", "opp:attack", "opp:target"]],
  ["all", [...STOP_STEPS.flatMap((s) => ["own:" + s, "opp:" + s]).filter((c) => c !== "own:declare_blockers"), "opp:spell", "opp:attack", "opp:target"]],
  ["none", []],
];

// view の turn から、step の名前（op step の to と同じ。メインは main1 / main2）
// 進める・終えるボタンのアイコン（線画の SVG の path。色は文字色に合わせる）
const SVG_NS = "http://www.w3.org/2000/svg";
const ICON = {
  upkeep: "M4 17h16M7 17a5 5 0 0 1 10 0M12 4v3M5.6 8.6l2.1 2.1M18.4 8.6l-2.1 2.1",                          // 日の出
  draw: "M9 7h9a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zM5 17V4a1 1 0 0 1 1-1h8",     // 重ねたカード
  main1: "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM10 8l2-1v8",              // カード（1）
  beginning_of_combat: "M6 21V4M6 4h11l-2 4 2 4H6",                                                     // 旗
  declare_attackers: "M4 4l11 11M15 15l-2 3M15 15l3-2M20 4L9 15M9 15l2 3M9 15l-3-2M3 21l3-3M21 21l-3-3", // 剣を交差
  declare_blockers: "M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z",                                     // 盾
  combat_damage: "M12 2l2.5 6.5L21 9l-5 4.5L17.5 21 12 17l-5.5 4L8 13.5 3 9l6.5-.5z",                      // 星（ダメージ）
  end_of_combat: "M19 5L8 16M15 5h4v4M6 14l4 4M4 20l3-3",                                                // 剣を収める
  main2: "M7 3h10a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM10 8h4v3.5h-4V15h4",     // カード（2）
  end: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",                                             // 月（終了ステップ）
  next: "M5 5l7 7-7 7M12 5l7 7-7 7",                                                                    // 次へ（»）
  endTurn: "M5 5l9 7-9 7zM18 5v14",                                                                     // 最後まで（▶|）
  pass: "M12 20V5M6 11l6-6 6 6",                                                                        // 相手に渡す（↑）
};
// 進めるボタンの並び: フェイズごとの枠（見出しは止める場所の表と同じ）を、ターンの順に1行に置く。
// アンタップ・ステップは優先権が無く止まれないので、ボタンは無い（開始フェイズはアップキープから）
const STEP_GROUPS = [
  { group: "beginning", steps: ["upkeep", "draw"] }, { group: "main1", steps: ["main1"] },
  { group: "combat", steps: ["beginning_of_combat", "declare_attackers", "declare_blockers", "combat_damage", "end_of_combat"] },
  { group: "main2", steps: ["main2"] }, { group: "ending", steps: ["end"] },
];
// 「次へ」のボタンに出す行き先（枠の見出しが無いので、どのフェイズか分かる名前）は辞書の play.nextShort.<ステップ>

export function stepName(t) { return t.step === "main" ? t.phase : t.step; }

// ステップのフェイズ（view の turn.phase と同じ名前）
export const STEP_PHASE = { untap: "beginning", upkeep: "beginning", draw: "beginning", main1: "main1",
  beginning_of_combat: "combat", declare_attackers: "combat", declare_blockers: "combat", combat_damage: "combat",
  end_of_combat: "combat", main2: "main2", end: "ending", cleanup: "ending" };

export function nextStep(t) {
  const i = STEPS.indexOf(stepName(t));
  return i >= 0 && i < STEPS.length - 1 ? STEPS[i + 1] : null;
}

// ゲーム前に、その Player がキープを宣言したか・何回マリガンしたか（宣言の記録から）
export function hasKept(v, pid) {
  return v.declarations.some((d) => d.turn === 0 && d.player === pid && d.kind === "keep");
}
export function mulligans(v, pid) {
  return v.declarations.filter((d) => d.turn === 0 && d.player === pid && d.kind === "mulligan").length;
}

// その Player への、まだ答えていない審判の質問（無ければ null）
export function myAsk(v, pid) {
  return ((v.requests || {}).asks || []).find((a) => a.player === pid) || null;
}

// カードがどの領域にあるか（view の中で）
export function zoneOf(v, id) {
  for (const [key, z] of Object.entries(v.zones)) {
    for (const part of ["cards", "known", "known_positions", "known_unordered"]) {
      if ((z[part] || []).some((c) => c.id === id)) return key;
    }
  }
  return null;
}

export function findCard(v, id) {
  for (const z of Object.values(v.zones)) {
    for (const part of ["cards", "known", "known_positions", "known_unordered"]) {
      const c = (z[part] || []).find((x) => x.id === id);
      if (c) return c;
    }
  }
  return null;
}

// op を短い文に（下書きの一覧とラベル用。画面の言語で）
export function describeOp(op, nm = (id) => id) {
  const ref = (r) => (Array.isArray(r) ? r.map(ref).join(", ")
    : r && typeof r === "object" ? JSON.stringify(r) : nm(r));
  const zone = (z) => (["graveyard", "hand", "library", "battlefield", "exile"].includes(z) ? tr(`play.zone.${z}`) : z);
  switch (op.op) {
    case "move": return tr("play.op.move", { card: ref(op.card), zone: zone(op.to), bottom: op.position === "bottom" ? tr("play.op.bottom") : "" });
    case "damage": return tr("play.op.damage", { target: ref(op.target), n: op.amount });
    case "life_loss": return op.player ? tr("play.op.lifeLoss", { player: ref(op.player), n: op.amount }) : tr("play.op.lifeLoss.any", { n: op.amount });
    case "life_gain": return op.player ? tr("play.op.lifeGain", { player: ref(op.player), n: op.amount }) : tr("play.op.lifeGain.any", { n: op.amount });
    case "tap": return tr("play.op.tap", { card: ref(op.card) });
    case "untap": return tr("play.op.untap", { card: ref(op.card) });
    case "attack": return tr("play.op.attack", { attacker: ref(op.attacker), target: ref(op.target) });
    case "block": return tr("play.op.block", { blocker: ref(op.blocker), attacker: ref(op.attacker) });
    case "counter_add": return tr("play.op.counterAdd", { target: ref(op.target), kind: op.kind, n: op.amount || 1 });
    case "counter_remove": return tr("play.op.counterRemove", { target: ref(op.target), kind: op.kind, n: op.amount || 1 });
    case "note_add": return tr("play.op.noteAdd", { target: ref(op.target), text: op.text });
    case "step": return tr("play.op.step", { step: stepLabel(op.to) });
    case "stack_remove": return op.card_to === "graveyard" && op.item ? tr("play.op.counterItem", { item: ref(op.item) }) : tr("play.op.resolved");
    case "draw": return tr("play.op.draw", { n: op.count || 1 });
    case "declare": return tr("play.op.declare", { kind: op.kind, text: op.text ? " " + op.text : "" });
    case "create": return tr("play.op.create", { name: op.name || "" });
    default: return `${op.op} ${JSON.stringify(Object.fromEntries(Object.entries(op).filter(([k]) => k !== "op")))}`;
  }
}

// ---------------------------------------------------------------- 下書き（純粋な部品）

// 「その後」に書けるステップ（play.py の STEP_THEN と同じ）
export const STEP_THEN = ["upkeep", "draw", "main1", "beginning_of_combat", "declare_attackers", "declare_blockers",
  "combat_damage", "end_of_combat", "main2", "end"];

// 下書きの行（送る plan の1行）を、送る形だけにする
export function cleanLine(l) {
  const o = { kind: l.kind || "other", text: l.text };
  if (l.cards && l.cards.length) o.cards = [...l.cards];
  if (l.targets && l.targets.length) o.targets = [...l.targets];
  if (l.count) o.count = l.count;
  if (l.kind === "step" && l.to) o.to = l.to;
  if (l.kind === "then" && l.then) o.then = l.then;
  if (TAPS.includes(l.tap)) o.tap = l.tap;
  if (l.no_block) o.no_block = true;
  return o;
}

// 「その後」の短い名前（送るボタン・下書きの欄に出す）
export function thenLabel(then) {
  if (["continue", "pass", "resolve", "end_turn", "turn_start"].includes(then)) return tr(`play.then.${then}`);
  return STEPS.includes(then) ? tr("play.then.step", { step: stepLabel(then) }) : then;
}

// 下書きの行から、審判に送る依頼 {plan, then} を作る。最後の行が「その後」の行（ターン終了・パス）か、ステップを
// 進める行なら、それを「その後」にする（行からは外す）。無ければ、自分のターンは続ける・相手のターンはパス
// （相手のクリンナップで自分の番なら、自分のターンを始める）。途中の「その後」の行は、文として審判に渡す
export function toRequest(lines, turn, seat) {
  const plan = lines.map(cleanLine);
  const last = plan[plan.length - 1];
  let then = null;
  if (last && last.kind === "then" && last.then) then = plan.pop().then;
  else if (last && last.kind === "step" && STEP_THEN.includes(last.to)) then = plan.pop().to;
  if (!then) then = turn.active === seat ? "continue"
    : turn.step === "cleanup" && turn.waiting_on === seat ? "turn_start" : "pass";
  return { plan: plan.map((l) => (l.kind === "then" ? { kind: "other", text: l.text } : l)), then };
}

// 途中で止まった計画の残り（view.resume: 相手の割り込み・本人が決める所で止まった）を、下書きの行に戻す。
// 「その後」は最後の行にする（ターン終了・パスは「その後」の行、ステップならステップを進める行）
export function resumeLines(resume) {
  const lines = (resume.lines || []).map(cleanLine);
  const t = resume.then;
  if (t === "end_turn" || t === "pass") lines.push({ kind: "then", then: t, text: tr(t === "pass" ? "play.passToOpp" : "play.endTurn") });
  else if (STEP_THEN.includes(t)) lines.push({ kind: "step", to: t, text: tr("play.goTo", { step: stepLabel(t) }) });
  return lines;
}

// 「審判に依頼」のボタンが今することと、その名前。下書きが空でパスになるなら、審判を通さずにパスの宣言にする
// （自分のターンでも、相手の呪文・能力がスタックにあれば）。自分のターンで下書きもスタックも空なら、次のステップへ。
// 何も送れなければ null（相手のターンで優先権が無い）
export function requestAction(lines, comment, turn, seat, stackSize) {
  const r = toRequest(lines, turn, seat);
  const empty = !r.plan.length && !comment;
  if (empty && (r.then === "pass" || (r.then === "continue" && stackSize > 0))) {
    return turn.priority === seat ? { kind: "pass", label: tr("play.pass") } : null;
  }
  // 依頼はゲーム前か、待たれている（優先権・ブロック・自分の番）ときだけ。相手が考えている間は下書きを作っておく
  if (turn.turn !== 0 && turn.waiting_on !== seat) return null;
  if (empty && r.then === "continue") {
    // 自分のターンで下書きが空: 次のステップへ進める（終了ステップなら、ターンを終える）。何もせずに止まらないように
    const next = nextStep(turn);
    r.then = STEP_THEN.includes(next) ? next : "end_turn";
  }
  return { kind: "request", ...r, label: tr("play.sendWithThen", { then: thenLabel(r.then) }) };
}

// 下書き・送った依頼の行から、画面に出す予定: 手札に足す「？」の枚数と、束に付ける印、ブロックの予定（戦闘の欄）。
// 中身はまだ誰も知らない（審判が処理した後に、席の view として初めて届く）
export function planned(lines, seat) {
  const out = { hand: {}, piles: {}, blocks: [] };
  const note = (key, text) => { (out.piles[key] = out.piles[key] || []).push(text); };
  const lib = `${seat}.library`;
  for (const l of lines) {
    const n = l.count || 1;
    if (l.kind === "draw") { out.hand[seat] = (out.hand[seat] || 0) + n; note(lib, tr("play.planned.draw", { n })); }
    else if (l.kind === "look") note(lib, tr("play.planned.look", { n }));
    else if (l.kind === "reveal") note(lib, tr("play.planned.reveal", { n }));
    else if (l.kind === "shuffle") note(lib, tr("play.planned.shuffle"));
    else if (l.kind === "search") note(lib, tr("play.planned.search"));
    else if (l.kind === "mill") note(`${(l.targets || [])[0] || seat}.library`, tr("play.planned.mill", { n }));
    else if (l.kind === "block" && (l.cards || [])[0] && (l.targets || [])[0]) out.blocks.push({ blocker: l.cards[0], attacker: l.targets[0] });
  }
  return out;
}

// ブロックを決める所か: 相手のターンの攻撃・ブロックのステップで攻撃されていて、自分の番（審判の質問・処理中でない）、
// まだ自分のクリーチャーのブロックが書かれていない（play.py の blocks_undecided と同じ場面）
export const BLOCK_STEPS = ["declare_attackers", "declare_blockers"];
export function blockTime(v, seat) {
  const t = v.turn;
  if (t.turn === 0 || t.active === seat || t.waiting_on !== seat || !BLOCK_STEPS.includes(t.step)) return false;
  if (!v.combat.attacks.length || myAsk(v, seat)) return false;
  const mine = new Set((v.zones.battlefield.cards || []).filter((c) => (c.controller || c.owner) === seat).map((c) => c.id));
  return !v.combat.blocks.some((b) => mine.has(b.blocker));
}

// 攻撃先の候補: 相手の Player、相手がコントロールするプレインズウォーカー、自分がコントロールするバトル
// （包囲戦は唱えた Player の相手が守るので、自分のバトルを攻撃する）。攻撃できるかの判断は審判が行う
export function attackTargets(v, seat, opponent) {
  const bf = v.zones.battlefield.cards || [];
  const ctl = (c) => c.controller || c.owner;
  return [opponent,
    ...bf.filter((c) => c.attackable === "planeswalker" && ctl(c) === opponent).map((c) => c.id),
    ...bf.filter((c) => c.attackable === "battle" && ctl(c) === seat).map((c) => c.id)];
}

// カードの Note「mana: …」（土地を出したときに書く、出せるマナ）から、タップして出すマナの候補を読む。
// 読むのは記号を「or」「,」でつないだだけの基本の書き方だけ: "mana: {G} or {U}" → {G}・{U}、"mana: {C}{C}" → {C}{C}。
// それ以外（条件・任意の色・ライフなど）は Note の文のまま { note } として返し、メニューでは1つにまとめる（出すマナは人が書く）
const BASIC_MANA_RE = /^(?:\{[WUBRGC0-9]\})+(?:\s*(?:,\s*)?(?:\bor\b)?\s*(?:\{[WUBRGC0-9]\})+)*$/i;
export function manaChoices(notes) {
  const out = [];
  for (const n of notes || []) {
    const m = /^\s*mana\s*:\s*(.*)$/is.exec(n.text || "");
    if (!m) continue;
    const body = m[1].trim();
    if (!BASIC_MANA_RE.test(body)) { out.push({ note: body }); continue; }
    for (const mana of body.match(/(?:\{[^{}]+\})+/g)) {
      if (!out.some((o) => o.mana === mana)) out.push({ mana });
    }
  }
  return out;
}

// 行のカードをタップするか（tap: 起動のコストの {T}・メニューの「タップ」、untap: メニューの「アンタップ」、none: 攻撃でタップしない）。
// 盤面の予定はこの値で決め、文は読まない（文は画面の言語で変わる）。値の無い古い行（この値を足す前の下書き・送った依頼）だけ、文から読む
export const TAPS = ["tap", "untap", "none"];
const TAP_RE = /をタップして/;
const UNTAP_RE = /をアンタップする/;
const TAP_LINE_RE = /をタップする/;
export function tapOf(l) {
  if (TAPS.includes(l.tap)) return l.tap;
  const text = l.text || "";
  if (l.kind === "activate") return TAP_RE.test(text) ? "tap" : null;
  if (l.kind === "attack") return /タップしない/.test(text) ? "none" : "tap";
  if (l.kind === "other") return UNTAP_RE.test(text) ? "untap" : TAP_LINE_RE.test(text) ? "tap" : null;
  return null;
}
// 「ブロックしない」の行か（no_block。値の無い古い行は文で）
export const noBlock = (l) => !!l.no_block || (l.kind === "other" && l.text === "ブロックしない");

// 下書き・送った依頼の行を、見るためだけに盤面へ仮に反映した view の写し（卓は動かさない。審判の処理で本物に替わる）。
// 反映するのは盤面の動きが決まっている行だけ: 土地を出す（手札 → 戦場）・唱える（手札 → スタック）・起動（スタックに能力。
// タップしてなら発生源をタップ）・解決（スタックから外し、呪文はパーマネントなら戦場・インスタント／ソーサリーなら墓地へ）・
// 攻撃（戦闘に加え、警戒でなければタップ）・タップ／アンタップする（メニューの行）・ステップを進める（フェイズの表示）・引く・切削（ライブラリーの枚数）。それ以外の行は印と文だけ。
// 仮のものには planned を付ける（スタックの項目の id は plan<行の番号>）
export function preview(v, lines, seat) {
  if (!v || !lines.length) return v;
  const out = structuredClone(v);
  const take = (key, id) => {
    const z = out.zones[key];
    if (!z) return null;
    for (const part of ["cards", "known"]) {
      const i = (z[part] || []).findIndex((c) => c.id === id);
      if (i < 0) continue;
      const [c] = z[part].splice(i, 1);
      if (typeof z.count === "number") z.count = Math.max(0, z.count - 1);
      return c;
    }
    return null;
  };
  const put = (key, c) => {
    const z = out.zones[key] || (out.zones[key] = { count: 0, cards: [] });
    (z.cards = z.cards || []).push(c);
    if (typeof z.count === "number") z.count += 1;
  };
  const shrink = (key, n) => { const z = out.zones[key]; if (z && typeof z.count === "number") z.count = Math.max(0, z.count - n); };
  const bf = (id) => (out.zones.battlefield.cards || []).find((c) => c.id === id);
  const hand = `${seat}.hand`;
  const manaIds = new Set((out.players || []).flatMap((p) => (p.mana || []).map((m) => m.id)));
  const manaOf = (targets) => (targets || []).length > 0 && targets.every((t) => manaIds.has(t));
  lines.forEach((l, i) => {
    const id = (l.cards || [])[0];
    const item = `plan${i}`;
    const aim = () => { if ((l.targets || []).length) out.links.push({ source: item, kind: "target", targets: [...l.targets], planned: true }); };
    if (l.kind === "play_land" && id) {
      const c = take(hand, id);
      if (c) put("battlefield", { ...c, controller: seat, land: true, tapped: false, planned: true });
    } else if (l.kind === "cast" && id) {
      const c = take(hand, id);
      if (!c) return;
      put("stack", { ...c, planned: true });
      out.stack.unshift({ id: item, kind: "spell", card: id, controller: seat, text: "", planned: true });
      aim();
    } else if (l.kind === "activate" && id) {
      if (tapOf(l) === "tap" && bf(id)) bf(id).tapped = true;
      out.stack.unshift({ id: item, kind: "activated", source: id, controller: seat, text: l.text, planned: true });
      aim();
    } else if (l.kind === "resolve") {
      // 解決する項目: スタックの id（本物）か、カード（唱えた呪文・起動した能力の発生源）で上から探す
      const sid = (l.targets || []).find((t) => out.stack.some((s) => s.id === t));
      const at = sid ? out.stack.findIndex((s) => s.id === sid)
        : out.stack.findIndex((s) => id && (s.card === id || s.source === id));
      if (at < 0) return;
      const [s] = out.stack.splice(at, 1);
      out.links = out.links.filter((k) => k.source !== s.id);
      if (s.card) {
        const c = take("stack", s.card);
        const to = ((v.card_kinds || {})[s.card] || {}).to || "battlefield";
        if (c) put(to === "graveyard" ? `${s.controller || seat}.graveyard` : "battlefield",
          { ...c, controller: s.controller || seat, tapped: false, planned: true });
      }
    } else if (l.kind === "attack" && id && bf(id)) {
      if (tapOf(l) !== "none") bf(id).tapped = true;
      out.combat.attacks.push({ attacker: id, target: (l.targets || [])[0], planned: true, line: i });
    } else if (l.kind === "other" && id && bf(id) && tapOf(l) === "untap") {
      bf(id).tapped = false;
    } else if (l.kind === "other" && id && bf(id) && tapOf(l) === "tap") {
      bf(id).tapped = true;
    } else if (l.kind === "other" && manaOf(l.targets)) {
      // マナを使う行（targets にプールのマナの id）: count があればその数、無ければ全部を減らす
      for (const t of l.targets) {
        for (const p of out.players) {
          const m = p.mana.find((x) => x.id === t);
          if (m) m.amount = l.count && l.targets.length === 1 ? Math.max(0, m.amount - l.count) : 0;
        }
      }
      for (const p of out.players) p.mana = p.mana.filter((m) => m.amount > 0);
    } else if (l.kind === "step" && STEP_PHASE[l.to]) {
      // 予定のステップへ進める（フェイズの表示も変わる）。戦闘を抜けたら予定の攻撃は外す
      const main = l.to === "main1" || l.to === "main2";
      out.turn = { ...out.turn, phase: STEP_PHASE[l.to], step: main ? "main" : l.to, planned: true };
      if (STEP_PHASE[l.to] !== "combat") out.combat = { ...out.combat, attacks: out.combat.attacks.filter((a) => !a.planned) };
    } else if (l.kind === "draw") {
      shrink(`${seat}.library`, l.count || 1);
    } else if (l.kind === "mill") {
      shrink(`${(l.targets || [])[0] || seat}.library`, l.count || 1);
    }
  });
  return out;
}

// 下書き・送った依頼の行で使うカード・対象（id → 印の class）
export function planMarks(lines) {
  const m = new Map();
  for (const l of lines) for (const id of [...(l.cards || []), ...(l.targets || [])]) m.set(id, "m-plan");
  return m;
}

// ---------------------------------------------------------------- 画面の部品


// 小さなメニュー。items: [{label, fn, hint?, nodes?}] と "-"（区切り）。nodes があれば label の代わりにそれを並べる（マナ・シンボルの画像など）
function openMenu(ev, title, items) {
  closeMenu();
  const box = $("menu");
  box.replaceChildren();
  if (title) box.append(el("div", "mtitle", title));
  for (const it of items) {
    if (it === "-") { box.append(el("div", "msep")); continue; }
    if (!it) continue;
    const b = el("button", "mitem", it.nodes ? null : it.label);
    if (it.nodes) { b.append(...it.nodes); b.setAttribute("aria-label", it.label); }
    if (it.hint) b.title = it.hint;
    b.onclick = () => { closeMenu(); it.fn(); };
    box.append(b);
  }
  box.hidden = false;
  const w = box.offsetWidth, h = box.offsetHeight;
  box.style.left = `${Math.max(4, Math.min(ev.clientX, innerWidth - w - 4))}px`;
  box.style.top = `${Math.max(4, Math.min(ev.clientY, innerHeight - h - 4))}px`;
  ev.stopPropagation();
}
function closeMenu() { const m = $("menu"); if (m) m.hidden = true; }

// 入力欄つきの問い合わせ。fields: [{name, label, value, type}]、choices: [{label, value}]（押したら即決）
function ask({ title, fields = [], choices = null, ok = "OK" }) {
  return new Promise((resolve) => {
    const dlg = $("ask");
    const form = el("form", "askform");
    form.method = "dialog";
    form.append(el("div", "atitle", title));
    const inputs = {};
    for (const f of fields) {
      const row = el("label", "arow");
      const input = el("input");
      input.type = f.type || "text";
      if (input.type === "checkbox") input.checked = !!f.value;
      else input.value = f.value ?? "";
      if (f.type === "number") input.min = "0";
      input.setAttribute("aria-label", f.label);
      inputs[f.name] = input;
      row.append(el("span", null, f.label), input);
      form.append(row);
    }
    let result = null;
    if (choices) {
      const row = el("div", "achoices");
      for (const c of choices) {
        const b = el("button", null, c.label);
        b.type = "button";
        b.onclick = () => { result = c.value; dlg.close(); };
        row.append(b);
      }
      form.append(row);
    }
    const buttons = el("div", "abuttons");
    const cancel = el("button", null, tr("play.cancel"));
    cancel.type = "button";
    cancel.onclick = () => dlg.close();
    buttons.append(cancel);
    if (fields.length) {
      const submit = el("button", "on", ok);
      submit.type = "submit";
      buttons.append(submit);
      form.onsubmit = () => {
        result = Object.fromEntries(Object.entries(inputs).map(([k, i]) =>
          [k, i.type === "checkbox" ? i.checked : i.type === "number" ? Number(i.value) : i.value.trim()]));
      };
    }
    form.append(buttons);
    dlg.replaceChildren(form);
    dlg.onclose = () => resolve(result);
    dlg.showModal();
    const first = form.querySelector("input");
    if (first) { first.focus(); if (first.select) first.select(); }
  });
}

// 時間切れの知らせ（公開のサーバーの人間どうしの対局）: 相手（人間）の番が続いている分。idle はサーバーが時系列に付けた
// {seconds, notice, limit, humans} と、受け取った時刻 at。知らせる前・自分の番・審判や AI の番なら null
export function idleState(idle, wait, seat, now) {
  if (!idle || !wait || wait === seat || wait === "judge" || !(idle.humans || []).includes(wait)) return null;
  const seconds = idle.seconds + Math.max(0, now - idle.at) / 1000;
  if (seconds < idle.notice) return null;
  return { who: wait, minutes: Math.floor(seconds / 60), canClaim: seconds >= idle.limit,
    left: Math.max(1, Math.ceil((idle.limit - seconds) / 60)) };
}

// 長い文を n 文字ほどで切る（メニューの名前）
const short = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);

let toastTimer = null;
export function toast(text, error) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, error ? 6000 : 2500);
}

// ---------------------------------------------------------------- 操作

export function createPlay(ui, { source, reload, showCard, toggleOpen, render, manaNodes }) {
  let compose = null;  // 組み立て中の行（唱える・起動・対象を選ぶ）
  let pick = null;     // 次のクリックで選ぶもの（ブロック先の攻撃クリーチャーなど）
  let blocker = null;  // ブロックを決める所で選んだ自分のクリーチャー（次に押した攻撃クリーチャーをブロックする）
  let busy = false;
  let draft = null;    // 下書き {key, lines, comment}。送るまでブラウザの中だけ（localStorage に対局・席ごと）
  let sent = null;     // 送った依頼の行。審判が処理するまで、印と「？」を出したままにする
  let sentFrom = 0;    // 送る前の cursor。盤面がこれより先に進んでから（依頼が届いた盤面で）処理済みかを見る
  const chosen = { seq: null, cards: [] };  // カードを選ぶ審判の質問で、今選んでいるカード
  // カードを選ぶ質問で自動で開いた束（墓地・追放など）。開くのは質問ごとに1回だけ（閉じて盤面を見られる）、答えたら閉じる
  const autoOpened = { seq: null, zones: [] };
  // 盤面のカードの印は、下書き・組み立て中の行・カードを選ぶ質問から毎回作る（描画のたびに読む）
  Object.defineProperty(ui, "marks", { get: () => marks(), set() {}, configurable: true });
  // 下書きの「？」（手札に足す枚数・束の印）。render が読む
  Object.defineProperty(ui, "planned", { get: () => plannedNow(), set() {}, configurable: true });

  const me = () => ui.play.seat;
  const opp = () => (ui.view.players.find((p) => p.id !== me()) || {}).id;
  // 画面に出す名前（日本語の画面では日本語名）。依頼の文（審判が読む）には英語名と id を書く（ref）
  const nm = (id) => (ui.names[id] ? `<${displayName(ui.names[id])}>` : id);
  const ref = (id) => (ui.names[id] ? `<${ui.names[id]}> (${id})` : id);
  const isCard = (id) => typeof id === "string" && !!findCard(ui.view, id);
  const ready = () => ui.play && ui.view && ui.live && !busy;
  const judging = () => !!ui.view && ui.view.turn.waiting_on === "judge";
  // 審判を待っている間（最新の盤面で）は何も操作しない: 盤面は詳細を見るだけ、パネルのボタンは全部押せない
  const locked = () => !!ui.play && ui.live && judging();
  const asked = () => !!ui.view && !!myAsk(ui.view, me());

  // ---- 下書き

  const draftKey = () => `draft.${ui.game}.${me()}`;
  function loadDraft() {
    if (draft && draft.key === draftKey()) return draft;
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(draftKey()) || "null"); } catch { saved = null; }
    draft = { key: draftKey(), lines: Array.isArray(saved && saved.lines) ? saved.lines : [],
      comment: saved && typeof saved.comment === "string" ? saved.comment : "",
      resumed: saved && typeof saved.resumed === "string" ? saved.resumed : "" };
    return draft;
  }
  function saveDraft() {
    try {
      if (draft.lines.length || draft.comment || draft.resumed) {
        localStorage.setItem(draft.key, JSON.stringify({ lines: draft.lines, comment: draft.comment, resumed: draft.resumed }));
      }
      else localStorage.removeItem(draft.key);
    } catch { /* 保存できなくても、このページの間は下書きを使える */ }
  }
  const hasDraft = () => { const d = loadDraft(); return d.lines.length > 0 || !!d.comment; };

  function line(kind, text, { cards = [], targets = [], count = 0, to = null, tap = null, no_block = false } = {}) {
    return cleanLine({ kind, text, cards: cards.filter(isCard), targets, count, to, tap, no_block });
  }

  function addLine(l) {
    if (!ui.play || !ui.view) return;
    if (!ui.live) return toast(tr("play.toast.live"), true);
    if (judging()) return toast(tr("play.toast.judging"), true);
    if (ui.view.turn.turn === 0) return toast(tr("play.toast.pregame"), true);
    const lines = loadDraft().lines;
    // 「その後」の行（ターン終了・パス）は最後に1つだけ: 足し直せば入れ替え、他の行はその前に入れる
    const end = lines.length && lines[lines.length - 1].kind === "then";
    if (end && l.kind === "then") lines[lines.length - 1] = l;
    else if (end) lines.splice(lines.length - 1, 0, l);
    else lines.push(l);
    saveDraft();
    refresh();
  }
  const other = (text, cards = [], targets = [], opts = {}) => addLine(line("other", text, { cards, targets, ...opts }));

  function removeLine(i) { loadDraft().lines.splice(i, 1); saveDraft(); refresh(); }
  function undoLine() { loadDraft().lines.pop(); saveDraft(); refresh(); }
  function clearDraft() { const d = loadDraft(); d.lines = []; d.comment = ""; saveDraft(); refresh(); }

  // 行に書いたカードが、今の盤面の思った所に無い（審判の処理で動いた）。送る前に本人が直す
  function staleLine(l) {
    const hand = `${me()}.hand`;
    return (l.cards || []).some((id) => !findCard(ui.view, id)
      || (["play_land", "cast"].includes(l.kind) && zoneOf(ui.view, id) !== hand));
  }

  function plannedLines() {
    if (!ui.play || !ui.view || !ui.live) return [];
    return [...(sent || []), ...loadDraft().lines];
  }
  function plannedNow() {
    return ui.play && ui.view ? planned(plannedLines(), me()) : null;
  }

  // 描画する view: 最新の盤面（Live）なら、下書き・送った依頼の行を仮に反映した写し（render の hooks.preview）。
  // メニューの判定にも、画面に見えている形（shownView）を使う
  let shownView = null;
  function previewView(v) {
    shownView = ui.play && ui.live && v ? preview(v, plannedLines(), me()) : v;
    return shownView;
  }

  // そのカードを使う下書きの行を外すメニュー（送った依頼の行は外せない）
  function unplanItems(id) {
    return loadDraft().lines.map((l, i) => ({ l, i }))
      .filter(({ l }) => (l.cards || []).includes(id) || (l.targets || []).includes(id))
      .map(({ l, i }) => ({ label: tr("play.unplan", { text: short(localize(l.text), 28) }),
        fn: () => removeLine(i), hint: tr("play.unplan.hint") }));
  }

  // 途中で止まった自分の計画（view.resume）: 下書きが空なら残りを戻す。下書きがあれば、戻すかを本人が選ぶ（resumeBox）。
  // 一度戻した・捨てた計画（id）は、もう出さない
  function pendingResume() {
    const r = ui.play && ui.live && ui.view && ui.view.resume;
    return r && loadDraft().resumed !== r.id ? r : null;
  }
  function takeResume(r, add = true, redraw = true) {
    const d = loadDraft();
    if (add) d.lines.push(...resumeLines(r));
    d.resumed = r.id;
    saveDraft();
    if (add) toast(tr("play.resume.toast", { of: r.of }));
    if (redraw) refresh();
  }
  function resumeBox(r) {
    const box = el("div", "compose");
    box.append(el("div", "ctitle", tr("play.resume.title", { of: r.of, n: r.lines.length, then: thenLabel(r.then) })),
      el("div", "chelp muted", tr(r.unknown ? "play.resume.unknown" : "play.resume.help")));
    const row = el("div", "crow");
    row.append(button(tr("play.resume.add"), () => takeResume(r), { cls: "on" }), button(tr("play.resume.discard"), () => takeResume(r, false)));
    box.append(row);
    return box;
  }

  // ---- 送信（依頼・宣言・回答だけ。卓の op は書かない）

  async function post(what, body, okText) {
    if (!ui.play) return false;
    if (!ui.live) { toast(tr("play.toast.live"), true); return false; }
    if (busy) return false;
    busy = true;
    renderPanel();
    let ok = false;
    try {
      const r = await source.post(ui.game, what, { ...body, seat: me(), expect: ui.cursor });
      const stopped = r.result.stopped;
      if (stopped) toast(tr("play.post.stopped", { reason: stopped.reason, error: stopped.error || "" }), true);
      else if (okText) toast(okText);
      ok = !stopped;
    } catch (e) {
      toast(e.stale ? tr("play.post.stale") : tr("play.post.failed", { message: e.message }), true);
    } finally {
      busy = false;
      await reload().catch(() => {});
      renderPanel();
    }
    return ok;
  }

  // 「審判に依頼」: 下書きの行から依頼（plan と「その後」）を作って送る。下書きが空でパスになるならパスの宣言
  async function sendDraft() {
    const d = loadDraft(), v = ui.view;
    const act = requestAction(d.lines, d.comment, v.turn, me(), v.stack.length);
    if (!act) return toast(tr("play.send.empty"), true);
    if (act.kind === "pass") return declareKind("pass");
    const lines = act.plan;
    const from = ui.cursor;
    blocker = null;
    if (await post("request", { plan: lines, then: act.then, comment: d.comment }, tr("play.send.ok"))) {
      sent = lines;
      sentFrom = from;
      d.lines = [];
      d.comment = "";
      saveDraft();
    }
    refresh();
  }

  const declareKind = (kind, text = "") => post("declare", { kind, text });
  const answer = (text, cards) => post("answer", cards ? { text, cards: [...cards] } : { text });

  // ---- 組み立て

  function refresh() {
    render();
  }

  // 自分に来ている、カードを選ぶ審判の質問（無ければ null）。選んでいるカードは質問ごとに持ち直す
  function cardAsk() {
    if (!ui.play || !ui.view || !ui.live || ui.view.turn.waiting_on !== me()) return null;
    const q = myAsk(ui.view, me());
    if (!q || !(q.cards || []).length) return null;
    if (chosen.seq !== q.seq) Object.assign(chosen, { seq: q.seq, cards: [] });
    return q;
  }

  function toggleChosen(q, id) {
    const i = chosen.cards.indexOf(id);
    if (i >= 0) chosen.cards.splice(i, 1);
    else if (q.pick[1] === 1) chosen.cards = [id];  // 1 枚なら押し直しで入れ替え
    else if (chosen.cards.length < q.pick[1]) chosen.cards.push(id);
    else return toast(tr("play.chooseUpTo", { n: q.pick[1] }), true);
    refresh();
  }

  function closeAutoOpened() {
    for (const z of autoOpened.zones) ui.open.delete(z);
    autoOpened.zones = [];  // seq は残す（答えを送った後、審判が処理するまで同じ質問が出ていても開き直さない）
  }

  function answerCards(q, cards) {
    closeAutoOpened();
    return answer(cards.length ? cards.map(ref).join(tr("play.listSep")) : tr("play.chooseNone"), cards);
  }

  function marks() {
    const m = planMarks(plannedLines());
    if (blocker && blocking()) m.set(blocker, "m-casting");
    const q = cardAsk();
    for (const id of q ? q.cards : []) m.set(id, chosen.cards.includes(id) ? "m-target" : "m-option");
    if (!compose) return m;
    if (compose.card) m.set(compose.card, "m-casting");
    if (compose.source) m.set(compose.source, "m-casting");
    for (const t of compose.targets || []) m.set(t, "m-target");
    return m;
  }

  function start(c) {
    if (judging()) return toast(tr("play.toast.judging"), true);
    compose = c;
    pick = null;
    refresh();
    return true;
  }

  function cancel() {
    compose = null;
    pick = null;
    refresh();
  }

  function toggleTarget(id) {
    const t = compose.targets;
    const i = t.indexOf(id);
    if (i >= 0) t.splice(i, 1); else t.push(id);
    refresh();
  }

  // 唱える・起動・対象を選ぶの組み立てを、下書きの1行にする
  function commit(resolve = false) {
    const c = compose;
    if (!c) return;
    const extra = c.text ? tr("paren", { text: c.text }) : "";
    if (c.kind === "target") {
      if (!c.targets.length) return toast(tr("play.pickTargets"), true);
      compose = null;
      return addLine(line("target", tr("play.line.target", { label: c.label, targets: c.targets.map(ref).join(", "), extra }), { targets: c.targets }));
    }
    const src = c.card || c.source;
    const aim = c.targets.length ? tr("play.line.aim", { targets: c.targets.map(ref).join(", ") }) : "";
    const key = c.kind === "cast" ? "play.line.cast" : c.tap ? "play.line.activateTap" : "play.line.activate";
    compose = null;
    addLine(line(c.kind, tr(key, { card: ref(src), aim, extra }), { cards: [src], targets: c.targets, tap: c.kind === "activate" && c.tap ? "tap" : null }));
    if (resolve === true) addLine(line("resolve", tr(c.kind === "cast" ? "play.line.resolve" : "play.line.resolveAbility", { card: ref(src) }), { cards: [src] }));
  }

  // ---- メニューの中身（どれも下書きに行を足すだけ）

  function moveItems(id, zone) {
    const mv = (to) => () => other(tr(`play.move.${to}.line`, { card: ref(id) }), [id]);
    return [
      zone !== "battlefield" && { label: tr("play.move.battlefield"), fn: mv("battlefield") },
      !zone.endsWith(".hand") && { label: tr("play.move.hand"), fn: mv("hand") },
      // graveyard: 手札のメニューでは「捨てる」があるので外す（cardItems）
      !zone.endsWith(".graveyard") && { label: tr(zone === "battlefield" ? "play.move.graveyard.bf" : "play.move.graveyard"), fn: mv("graveyard"), graveyard: true },
      zone !== "exile" && { label: tr("play.move.exile"), fn: mv("exile") },
      { label: tr("play.move.top"), fn: mv("top") },
      { label: tr("play.move.bottom"), fn: mv("bottom") },
    ];
  }

  async function counter(target, sign) {
    const r = await ask({ title: tr("play.counter.title", { card: nm(target) }), fields: [
      { name: "kind", label: tr("play.field.kind"), value: "+1/+1" }, { name: "amount", label: tr("play.field.count"), value: 1, type: "number" }] });
    if (!r || !r.kind || !r.amount) return;
    other(tr(sign > 0 ? "play.counter.add" : "play.counter.remove", { target: ref(target), kind: r.kind, n: r.amount }),
      [target], isCard(target) ? [] : [target]);
  }

  async function note(target) {
    const r = await ask({ title: tr("play.note.title", { card: nm(target) }), fields: [
      { name: "text", label: tr("play.field.text"), value: "" }, { name: "eot", label: tr("play.note.eot"), type: "checkbox" }] });
    if (!r || !r.text) return;
    other(tr("play.note.line", { target: ref(target), text: r.text, eot: r.eot ? tr("play.note.eotSuffix") : "" }), [target]);
  }

  // 基本の書き方でない mana の Note の土地: 出すマナ（と払うライフなど）を書いてもらい、Note の文と合わせて審判に任せる
  async function tapForMana(id, note) {
    const r = await ask({ title: tr("play.mana.title", { card: nm(id), note }), fields: [{ name: "mana", label: tr("play.mana.field"), value: "" }] });
    if (!r) return;
    other(tr("play.mana.line", { card: ref(id), what: r.mana ? tr("play.mana.add", { mana: r.mana }) : tr("play.mana.any"), note }), [id], [], { tap: "tap" });
  }

  async function amount(title, fn, value = 1) {
    const r = await ask({ title, fields: [{ name: "n", label: tr("play.field.count"), value, type: "number" }] });
    if (r && r.n > 0) fn(Math.min(99, Math.floor(r.n)));
  }

  // 攻撃する: まず相手の Player を攻撃する行を足す。プレインズウォーカー・バトルへは、戦闘の欄で攻撃先を変える（retarget）
  const attackText = (id, target, tap) => tr("play.attack.line", { attacker: ref(id), target: ref(target), notap: tap ? "" : tr("play.attack.notap") });
  function attackItems(c) {
    const t = ui.view.turn;
    if (t.active !== me() || c.land) return [];
    const attack = (tap) => () => addLine(line("attack", attackText(c.id, opp(), tap), { cards: [c.id], targets: [opp()], tap: tap ? "tap" : "none" }));
    return [
      { label: tr("play.attack"), fn: attack(true) },
      { label: tr("play.attack.vigilance"), fn: attack(false) },
    ];
  }

  // 戦闘の欄の攻撃先（下書きの攻撃だけ）を押したときの処理。変えられないなら null（送った依頼・卓の攻撃・候補が1つ）
  function retarget(a) {
    if (!a.planned || !ready() || judging()) return null;
    const i = a.line - (sent || []).length;
    const l = loadDraft().lines[i];
    if (!l || l.kind !== "attack" || (l.cards || [])[0] !== a.attacker) return null;
    const targets = attackTargets(ui.view, me(), opp());
    if (targets.length < 2) return null;
    return async () => {
      const to = await ask({ title: tr("play.attack.targetTitle", { card: nm(a.attacker) }), choices: targets.map((id) =>
        ({ label: `${id === a.target ? "✓ " : ""}${attackLabel(ui.view, id)}`, value: id })) });
      if (!to || to === a.target || loadDraft().lines[i] !== l) return;
      const tap = tapOf(l) !== "none";
      loadDraft().lines[i] = line("attack", attackText(a.attacker, to, tap), { cards: [a.attacker], targets: [to], tap: tap ? "tap" : "none" });
      saveDraft();
      refresh();
    };
  }

  function attackLabel(v, id) {
    const c = (v.zones.battlefield.cards || []).find((x) => x.id === id);
    if (!c) return tr("play.attack.player", { id });
    const n = (c.counters || {})[c.attackable === "battle" ? "defense" : "loyalty"];
    return tr("play.attack.permanent", { card: nm(id), kind: tr(c.attackable === "battle" ? "play.attack.battle" : "play.attack.planeswalker"),
      n: n !== undefined ? ` ${n}` : "" });
  }

  function blockItems(c) {
    const v = ui.view;
    if (v.turn.active === me() || !v.combat.attacks.length || c.land) return [];
    return [{ label: tr("play.block.item"), fn: () => {
      const attackers = v.combat.attacks.map((a) => a.attacker);
      const doBlock = (attacker) => addLine(line("block", tr("play.block.line", { blocker: ref(c.id), attacker: ref(attacker) }),
        { cards: [c.id], targets: [attacker] }));
      if (attackers.length === 1) return doBlock(attackers[0]);
      pick = { prompt: tr("play.block.prompt", { card: nm(c.id) }), accept: (card) => {
        if (!attackers.includes(card.id)) { toast(tr("play.block.pickAttacker"), true); return; }
        pick = null;
        doBlock(card.id);
      } };
      renderPanel();
    } }];
  }

  function cardItems(c, zone) {
    const id = c.id;
    const mine = (c.controller || c.owner) === me();
    const items = [];
    if (zone === `${me()}.hand`) {
      const kind = (ui.view.card_kinds || {})[id];  // 種類が分からなければ両方出す
      items.push((!kind || kind.land) && { label: tr("play.item.playLand"), fn: () => addLine(line("play_land", tr("play.line.playLand", { card: ref(id) }), { cards: [id] })) },
        (!kind || kind.spell) && { label: tr("play.item.cast"), fn: () => start({ kind: "cast", card: id, targets: [], text: "" }) },
        { label: tr("play.item.reveal"), fn: () => other(tr("play.line.reveal", { card: ref(id) }), [id]) }, "-",
        { label: tr("play.discard"), fn: () => other(tr("play.discard.line", { card: ref(id) }), [id]) },
        ...moveItems(id, zone).filter((x) => x && !x.graveyard));
    } else if (zone === "battlefield") {
      if (mine) {
        items.push(c.tapped ? { label: tr("play.item.untap"), fn: () => other(tr("play.line.untap", { card: ref(id) }), [id], [], { tap: "untap" }) }
          : { label: tr("play.item.tap"), fn: () => other(tr("play.line.tap", { card: ref(id) }), [id], [], { tap: "tap" }) });
        // Note に書いたマナ能力: 出すマナを選んでタップする（条件は文に残し、満たすかは審判が見る）
        if (!c.tapped) {
          for (const { mana, note } of manaChoices(c.notes)) {
            if (mana) {
              const label = tr("play.item.tapFor", { mana });
              items.push({ label, nodes: manaNodes(label), fn: () => other(tr("play.line.tapFor", { card: ref(id), mana }), [id], [], { tap: "tap" }) });
            } else {
              const label = tr("play.item.tapForNote", { note });
              items.push({ label, nodes: manaNodes(label), fn: () => tapForMana(id, note) });
            }
          }
        }
        items.push({ label: tr("play.item.activate"), fn: () => start({ kind: "activate", source: id, targets: [], text: "" }) });
        items.push(...attackItems(c), ...blockItems(c));
      } else {
        items.push(c.tapped ? { label: tr("play.item.untap"), fn: () => other(tr("play.line.untap", { card: ref(id) }), [id], [], { tap: "untap" }) }
          : { label: tr("play.item.tapEffect"), fn: () => other(tr("play.line.tapEffect", { card: ref(id) }), [id], [], { tap: "tap" }) },
        { label: tr("play.item.damage"), fn: () => amount(tr("play.damageTo", { card: nm(id) }), (n) => other(tr("play.line.damage", { card: ref(id), n }), [id])) },
        { label: tr("play.item.control"), fn: () => other(tr("play.line.control", { card: ref(id) }), [id]) });
      }
      const inCombat = ui.view.combat.attacks.some((a) => a.attacker === id) || ui.view.combat.blocks.some((b) => b.blocker === id);
      if (inCombat) items.push({ label: tr("play.item.removeCombat"), fn: () => other(tr("play.line.removeCombat", { card: ref(id) }), [id]) });
      items.push("-", { label: tr("play.item.addCounter"), fn: () => counter(id, 1) });
      if (c.counters && Object.keys(c.counters).length) items.push({ label: tr("play.item.removeCounter"), fn: () => counter(id, -1) });
      items.push({ label: tr("play.item.addNote"), fn: () => note(id) });
      for (const n of c.notes || []) {
        items.push({ label: tr("play.item.removeNote", { text: short(n.text, 24) }), fn: () => other(tr("play.line.removeNote", { card: ref(id), text: n.text }), [id]) });
      }
      items.push("-", ...moveItems(id, zone));
      if (c.token) items.push({ label: tr("play.item.removeToken"), fn: () => other(tr("play.line.removeToken", { card: ref(id) }), [id]) });
    } else if (zone && zone.endsWith(".library")) {
      items.push(...moveItems(id, zone));
    } else if (zone === "stack") {
      const i = ui.view.stack.findIndex((s) => s.card === id);
      if (i >= 0) return stackItems(ui.view.stack[i], i);
    } else if (zone) {
      if (c.owner === me()) items.push({ label: tr("play.item.cast"), fn: () => start({ kind: "cast", card: id, targets: [], text: "" }) });
      items.push(...moveItems(id, zone));
    }
    items.push("-", { label: tr("play.item.details"), fn: () => showCard(c) });
    return items;
  }

  function stackItems(s, i) {
    const items = [];
    const label = s.card ? ref(s.card) : tr("play.abilityOf", { card: ref(s.source) });
    if (i === 0) {
      items.push({ label: tr("play.item.resolve"), fn: () => addLine(line("resolve", tr("play.line.resolveTop", { label }), { targets: [s.id] })),
        hint: tr("play.item.resolve.hint") });
    }
    if (s.controller === me() && !s.card) {
      items.push({ label: tr("play.item.targets"), fn: () => start({ kind: "target", item: s.id, label, targets: [], text: "" }) });
    }
    const c = s.card ? findCard(ui.view, s.card) : s.source && findCard(ui.view, s.source);
    if (c) items.push("-", { label: tr("play.item.details"), fn: () => showCard(c) });
    return items;
  }

  // ---- ブロック: 自分のクリーチャー → 攻撃クリーチャーの順に押すと、下書きに「ブロックする」の行を足す

  const blocking = () => !!ui.play && !!ui.view && ui.live && !busy && blockTime(ui.view, me());
  const blockLines = () => loadDraft().lines.map((l, i) => ({ l, i })).filter((x) => x.l.kind === "block");

  // ブロックを決める所で押したカードを扱う。扱ったら true（扱わないカードはいつものメニュー）
  function onBlockClick(c) {
    const v = ui.view;
    const attackers = v.combat.attacks.map((a) => a.attacker);
    const ids = c.ids || [c.id];
    if (ids.some((id) => attackers.includes(id))) {
      if (!blocker) { toast(tr("play.block.firstMine"), true); return true; }
      const attacker = ids.find((id) => attackers.includes(id));
      const b = blocker;
      blocker = null;
      addLine(line("block", tr("play.block.line", { blocker: ref(b), attacker: ref(attacker) }), { cards: [b], targets: [attacker] }));
      return true;
    }
    const mine = (c.controller || c.owner) === me() && zoneOf(v, ids[0]) === "battlefield";
    if (!mine || c.land || !c.base_pt) return false;
    const planned = blockLines();
    const used = new Set(planned.map((x) => x.l.cards[0]));
    // もう予定に入っているクリーチャーを押したら、その行を外す（まとめた ×N はまだ予定に無いものから選ぶ）
    const free = ids.find((id) => !used.has(id));
    if (!free) {
      const last = planned.filter((x) => ids.includes(x.l.cards[0])).pop();
      removeLine(last.i);
      return true;
    }
    if (c.tapped) { toast(tr("play.block.tapped"), true); return true; }
    blocker = blocker === free ? null : free;
    refresh();
    return true;
  }

  function blockBox() {
    const box = el("div", "compose blockbox");
    box.append(el("div", "ctitle", tr("play.block.title")));
    box.append(el("div", "chelp muted", blocker ? tr("play.block.helpChosen", { card: nm(blocker) }) : tr("play.block.help")));
    const lines = blockLines();
    if (lines.length) {
      const list = el("ol", "cops reqlines");
      for (const { l, i } of lines) {
        const li = el("li", null, `${nm(l.cards[0])} → ${nm(l.targets[0])}`);
        li.title = localize(l.text);
        const x = el("button", "chip", "×");
        x.title = tr("play.block.remove");
        x.onclick = () => removeLine(i);
        li.append(x);
        list.append(li);
      }
      box.append(list);
    }
    const row = el("div", "crow");
    const none = loadDraft().lines.some(noBlock);
    box.append(el("div", "chelp muted", tr("play.block.sendHelp")));
    row.append(button(tr("play.noBlocks"), () => { blocker = null; other(tr("play.noBlocks"), [], [], { no_block: true }); },
      { disabled: lines.length > 0 || none, title: tr("play.noBlocks.title") }));
    if (blocker) row.append(button(tr("play.block.again"), () => { blocker = null; refresh(); }));
    box.append(row);
    return box;
  }

  // ---- クリックの受け口（render の hooks）

  function onCard(c, ev) {
    if (!ui.play || !ui.view) return false;
    const card = c.ids ? { ...c, id: c.ids[0] } : c;
    if (!ui.live) return false;
    if (locked()) { showCard(c); return true; }  // 審判を待っている間は詳細を見るだけ
    const zone = zoneOf(shownView || ui.view, card.id);
    if (pick) { pick.accept(card); return true; }
    const q = cardAsk();
    if (q && q.cards.includes(card.id)) { toggleChosen(q, card.id); return true; }  // 審判の質問の候補を選ぶ
    if (compose) {
      // 唱えている呪文そのものは対象にならないので何もしない。起動する能力の発生源は自分自身を対象にできる。
      // 詳細は右クリック（長押し）で出す
      if (card.id !== compose.card) toggleTarget(card.id);
      return true;
    }
    if (blocking() && onBlockClick(c)) return true;
    const title = `${card.name ? `<${displayName(card.name)}>` : tr("play.card")} ${card.id}`;
    if (card.planned) {  // 下書き・送った依頼で仮に出したカード: 予定を外す・続きの予定を足す（本物になるのは審判の処理の後）
      const items = unplanItems(card.id);
      const next = zone === "battlefield" && (card.controller || card.owner) === me() ? [
        { label: tr("play.item.activate"), fn: () => start({ kind: "activate", source: card.id, targets: [], text: "", tap: false }),
          hint: tr("play.activateLater.hint") },
        card.tapped ? { label: tr("play.item.untap"), fn: () => other(tr("play.line.untap", { card: ref(card.id) }), [card.id], [], { tap: "untap" }) }
          : { label: tr("play.item.tap"), fn: () => other(tr("play.line.tap", { card: ref(card.id) }), [card.id], [], { tap: "tap" }) },
        "-"] : [];
      openMenu(ev, title + tr("play.plannedSuffix"), [...next,
        ...(items.length ? items : [{ label: tr("play.sentPlan"), fn: () => {} }]),
        "-", { label: tr("play.item.details"), fn: () => showCard(c) }]);
      return true;
    }
    const unplan = unplanItems(card.id);
    openMenu(ev, title, [...unplan, ...(unplan.length ? ["-"] : []), ...cardItems(card, zone)]);
    return true;
  }

  // 手札のカードを盤面へドラッグしたときの予定（render の hooks.drop）。place: battlefield / graveyard / exile /
  // library-top / library-bottom。できない場面なら null（ドラッグは手札の並べ替えだけになる）。
  // 返す label は、領域に入ったときに出す説明。run で下書きに行を足す
  function onDrop(id, place) {
    if (!ui.play || !ui.live || !ui.view || locked() || compose || pick || ui.view.turn.turn === 0) return null;
    const v = shownView || ui.view;
    if (zoneOf(v, id) !== `${me()}.hand`) return null;
    const card = findCard(v, id);
    if (!card || card.planned) return null;
    const mv = (key) => ({ label: tr(key, { card: nm(id) }), run: () => other(tr(key, { card: ref(id) }), [id]) });
    if (place === "battlefield") {
      const kind = (ui.view.card_kinds || {})[id] || {};  // 土地の面があれば土地として出す（唱えるならメニューから）
      if (kind.land) return { label: tr("play.drop.land", { card: nm(id) }), run: () => addLine(line("play_land", tr("play.line.playLand", { card: ref(id) }), { cards: [id] })) };
      return { label: tr("play.drop.cast", { card: nm(id) }), run: () => {
        addLine(line("cast", tr("play.line.cast", { card: ref(id), aim: "", extra: "" }), { cards: [id] }));
        addLine(line("resolve", tr("play.line.resolve", { card: ref(id) }), { cards: [id] }));
      } };
    }
    if (place === "graveyard") return mv("play.discard.line");
    if (place === "exile") return mv("play.move.exile.line");
    if (place === "library-top") return mv("play.move.top.line");
    if (place === "library-bottom") return mv("play.move.bottom.line");
    return null;
  }

  function onStack(s, i, ev) {
    if (!ui.play || !ui.live) return false;
    if (locked()) {
      const c = s.card ? findCard(ui.view, s.card) : s.source && findCard(ui.view, s.source);
      if (c) showCard(c);
      return true;
    }
    if (compose) { if (s.id !== compose.item) toggleTarget(s.id); return true; }
    if (s.planned) {  // 下書き・送った依頼で仮に積んだ項目（plan<行の番号>。番号は送った依頼の行から数える）
      const at = Number(s.id.slice(4)) - (sent || []).length;
      const what = tr(s.card ? "play.line.resolve" : "play.line.resolveAbility", { card: ref(s.card || s.source) });
      // 一番上なら、続けて解決する予定を足せる（呪文はカード、能力は発生源で、どの項目かを審判に伝える）
      const resolve = i === 0 ? [{ label: tr("play.item.resolve"), fn: () => addLine(line("resolve", what, { cards: [s.card || s.source] })),
        hint: tr("play.resolveNext.hint") }, "-"] : [];
      openMenu(ev, `${s.card ? nm(s.card) : tr("play.abilityOf", { card: nm(s.source) })}${tr("play.plannedSuffix")}`, [...resolve, ...(at >= 0
        ? [{ label: tr("play.removePlan"), fn: () => removeLine(at) }] : [{ label: tr("play.sentPlan"), fn: () => {} }])]);
      return true;
    }
    openMenu(ev, s.card ? nm(s.card) : tr("play.abilityOf", { card: nm(s.source) }), stackItems(s, i));
    return true;
  }

  function onPlayer(pid, ev) {
    if (!ui.play || !ui.live || locked()) return;
    if (compose) return toggleTarget(pid);
    const self = pid === me();
    const life = (how) => () => amount(tr(`play.life.${how}.title`, { player: pid }), (n) => other(tr(`play.life.${how}.line`, { player: pid, n }), [], [pid]));
    openMenu(ev, pid, [
      self && { label: tr("play.life.minus"), fn: () => other(tr("play.life.minus.line", { player: pid }), [], [pid]) },
      self && { label: tr("play.life.plus"), fn: () => other(tr("play.life.plus.line", { player: pid }), [], [pid]) },
      { label: tr("play.item.damage"), fn: () => amount(tr("play.damageTo", { card: pid }), (n) => other(tr("play.line.damage", { card: pid, n }), [], [pid])) },
      { label: tr("play.item.loseLife"), fn: life("lose") },
      { label: tr("play.item.gainLife"), fn: life("gain") },
      { label: tr("play.item.addCounter"), fn: () => counter(pid, 1) },
    ]);
  }

  // 浮いているマナ（自分のマナ・プール）を押したとき: 使う予定を下書きに足す（何に使うかは文で補う）。
  // 行の targets にマナの id を書くので、下書きの写しでもその分が減って見える
  function onMana(pid, m, ev) {
    if (!ui.play || !ui.live || locked() || pid !== me() || ui.view.turn.turn === 0) return false;
    const sym = `{${m.color}}`;
    const use = (n) => addLine(line("other", tr("play.pool.use.line", { sym, id: m.id, n }), { targets: [m.id], count: n }));
    const pool = (shownView || ui.view).players.find((p) => p.id === pid).mana;
    openMenu(ev, tr("play.pool.title", { sym, n: m.amount }), [
      { label: tr("play.pool.use1", { sym }), fn: () => use(1) },
      m.amount > 1 && { label: tr("play.pool.useN", { sym }), fn: () => amount(tr("play.pool.useN.title", { sym }), (n) => use(Math.min(n, m.amount)), m.amount) },
      m.amount > 1 && { label: tr("play.pool.useAll", { sym, n: m.amount }), fn: () => use(m.amount) },
      "-",
      { label: tr("play.pool.empty"), fn: () => addLine(line("other", tr("play.pool.empty"), { targets: pool.map((x) => x.id) })),
        hint: tr("play.pool.empty.hint") },
    ]);
    return true;
  }

  // 束: 自分のライブラリーから引く・見る・公開・シャッフルは、手札・束に「？」の予定を出すだけ（中身は審判の処理の後）
  function onPile(key, ev) {
    if (!ui.play || !ui.live) return false;
    const [pid, zone] = key.split(".");
    const open = { label: tr(ui.open.has(key) ? "play.pile.close" : "play.pile.open"), fn: () => toggleOpen(key) };
    if (zone !== "library") return false;
    const items = [];
    if (ui.view.turn.turn === 0 || locked()) {  // ゲーム前・審判を待っている間は中身を見るだけ  // ゲーム前はキープ・マリガンだけ（引き直しは審判が行う）
      openMenu(ev, tr("play.pile.library", { player: pid }), [open]);
      return true;
    }
    if (pid === me()) {
      items.push({ label: tr("play.pile.draw1"), fn: () => addLine(line("draw", tr("play.op.draw", { n: 1 }), { count: 1 })) },
        { label: tr("play.pile.drawN"), fn: () => amount(tr("play.pile.drawN.title"), (n) => addLine(line("draw", tr("play.op.draw", { n }), { count: n })), 2) },
        { label: tr("play.pile.look"), fn: () => amount(tr("play.pile.look.title"), (n) => addLine(line("look", tr("play.pile.look.line", { n }), { count: n }))) },
        { label: tr("play.pile.reveal"), fn: () => addLine(line("reveal", tr("play.pile.reveal.line"), { count: 1 })) },
        { label: tr("play.pile.shuffle"), fn: () => addLine(line("shuffle", tr("play.pile.shuffle.line"))) });
    }
    items.push({ label: tr("play.pile.mill"), fn: () => amount(tr("play.pile.mill.title"), (n) => addLine(
      line("mill", tr("play.pile.mill.line", { player: pid, n }), { count: n, targets: [pid] }))) },
    "-", open);
    openMenu(ev, tr("play.pile.library", { player: pid }), items);
    return true;
  }

  // ---- 操作パネル

  function button(text, fn, opts = {}) {
    const b = el("button", opts.cls || null, text);
    if (opts.title) b.title = opts.title;
    b.disabled = !ready() || locked() || !!opts.disabled;
    b.onclick = fn;
    return b;
  }

  // カードを選ぶ質問: 候補を押して選び（盤面のカードを押してもよい）、決定。0 枚でよければ「選ばない」
  function cardAskBox(q) {
    const [lo, hi] = q.pick;
    const n = chosen.cards.length;
    const box = el("div", "compose");
    box.append(el("div", "chelp muted", tr("play.ask.choose", { range: lo === hi ? tr("play.ask.one", { n: lo }) : tr("play.ask.range", { lo, hi }) })));
    // 候補が閉じた領域（墓地・追放など）にあれば、質問が来たときに1回だけ開いて盤面でも見えるようにする
    if (autoOpened.seq !== q.seq) {
      closeAutoOpened();
      autoOpened.seq = q.seq;
      for (const id of q.cards) {
        const z = zoneOf(ui.view, id);
        if (z && z !== "battlefield" && z !== "stack" && !z.endsWith(".hand") && !ui.open.has(z)) { ui.open.add(z); autoOpened.zones.push(z); }
      }
      if (autoOpened.zones.length) setTimeout(render);
    }
    const list = el("div", "crow");
    for (const id of q.cards) {
      const on = chosen.cards.includes(id);
      const b = button(nm(id), () => toggleChosen(q, id), { cls: "chip cand" + (on ? " on" : ""), title: tr("play.ask.candidate.title", { id }) });
      const c = findCard(ui.view, id);
      const info = button(tr("play.item.details"), () => showCard(c), { cls: "chip" });
      info.disabled = !c;
      list.append(b, info);
    }
    const ok = button(n ? tr("play.ask.confirmN", { n }) : tr("play.ask.confirm"), () => answerCards(q, chosen.cards),
      { cls: "on", disabled: n < lo || n > hi || !n });
    const none = lo === 0 && button(tr("play.chooseNone"), () => answerCards(q, []));
    const row = el("div", "crow");
    row.append(...[ok, none].filter(Boolean));
    box.append(list, row);
    return box;
  }

  // アイコンのボタン（線画の SVG。名前は title と aria-label に出す）
  // opts.text があれば、アイコンに短い名前を添える
  function iconButton(d, label, fn, opts = {}) {
    const b = button("", fn, { ...opts, cls: "ibtn" + (opts.text ? " withtext" : "") + (opts.cls ? " " + opts.cls : ""), title: opts.title || label });
    b.setAttribute("aria-label", label);
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
    b.append(svg);
    if (opts.text) b.append(el("span", "ilabel", opts.text));
    return b;
  }
  // 進める・終える: 自分のターンのステップを全部、フェイズごとの枠に分けて決まった位置に並べる。
  // 行けるのは下書きで進めた所（盤面の予定）より後だけで、今の所と今のフェイズの枠に印を付け、それより前は薄くする。
  // 相手のターンは押せない。下の段は 次のステップ・ターン終了・パス。押すと下書きに行を足す（最後の行が「その後」になる）
  function stepButtons(off, mine) {
    const t = (shownView || ui.view).turn;
    const i = STEPS.indexOf(stepName(t));
    const go = (s) => () => addLine(line("step", tr("play.goTo", { step: stepLabel(s) }), { to: s }));
    const then = (k, text) => () => addLine({ kind: "then", then: k, text });
    const next = mine ? STEPS.slice(i + 1).find((s) => STEP_THEN.includes(s)) : null;
    const phase = STEP_PHASE[stepName(t)];
    const grid = el("div", "stepgrid");
    for (const group of STEP_GROUPS) {
      const g = el("div", "stepgroup");
      g.style.flexGrow = group.steps.length;
      if (mine && group.steps.some((s) => STEP_PHASE[s] === phase)) g.classList.add("curphase");
      g.append(el("div", "sglabel", tr(`play.group.${group.group}`)));
      const btns = el("div", "sgbtns");
      for (const s of group.steps) {
        const cur = mine && STEPS.indexOf(s) === i;
        const ok = mine && STEPS.indexOf(s) > i;
        const goTo = tr("play.goTo", { step: stepLabel(s) });
        btns.append(iconButton(ICON[s], goTo, go(s),
          { disabled: off || !ok, cls: cur ? "cur" : mine && !ok ? "past" : null,
            title: cur ? tr("play.step.now", { step: stepLabel(s) }) : goTo }));
      }
      g.append(btns);
      grid.append(g);
    }
    const acts = el("div", "stepacts");
    acts.append(iconButton(ICON.next, next ? tr("play.next.to", { step: stepLabel(next) }) : tr("play.next.any"),
        next ? go(next) : null, { disabled: off || !next, text: next ? tr("play.next.short", { step: tr(`play.nextShort.${next}`) }) : tr("play.next") }),
      iconButton(ICON.endTurn, tr("play.endTurn"), then("end_turn", tr("play.endTurn")),
        { disabled: off || !mine, text: tr("play.endTurn"), title: tr("play.endTurn.title") }),
      iconButton(ICON.pass, tr("play.passToOpp"), then("pass", tr("play.passToOpp")),
        { disabled: off || !mine, text: tr("play.pass"), title: tr("play.pass.title") }));
    return [grid, acts];
  }

  let freeFocus = false;  // 文の欄で足した後、描き直した欄にもう一度入力できるようにする

  // 依頼の欄: 下書きの行を並べ、送るボタンで「その後」を選ぶ。送る前は行を外せる（盤面は変わらない）。
  // ボタンに無い操作・補足（対象・モード・X など）は、文の欄に書いて Enter で1行として足す
  function requestBox() {
    const v = ui.view, t = v.turn, seat = me();
    const d = loadDraft();
    if (d.comment) {  // 前の形の「補足」が残っていたら、行にして見えるようにする（補足は送らない）
      d.lines.push(line("other", d.comment));
      d.comment = "";
      saveDraft();
    }
    const box = el("div", "reqbox");
    box.append(el("div", "phead2", tr("play.req.title")));
    if (d.lines.length) {
      const list = el("ol", "cops reqlines");
      d.lines.forEach((l, i) => {
        const bad = staleLine(l);
        const li = el("li", bad ? "stale" : null, localize(l.text));
        if (bad) li.title = tr("play.req.stale");
        const x = el("button", "chip", "×");
        x.title = tr("play.req.removeLine");
        x.disabled = locked();
        x.onclick = () => removeLine(i);
        li.append(x);
        list.append(li);
      });
      box.append(list);
    } else {
      box.append(el("div", "chelp muted", tr("play.req.empty")));
    }
    const off = judging() || asked();
    const free = el("input");
    free.placeholder = tr("play.req.free.placeholder");
    free.title = tr("play.req.free.title");
    free.disabled = off;
    free.onkeydown = (e) => {
      if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;  // 変換の確定の Enter は足さない
      e.preventDefault();
      const text = free.value.trim();
      if (!text) return;
      const n = loadDraft().lines.length;
      freeFocus = true;
      other(text);
      if (loadDraft().lines.length === n) freeFocus = false;  // 足せなかった（ゲーム前など）: 文は欄に残す
    };
    if (freeFocus) { freeFocus = false; setTimeout(() => free.focus()); }
    const r2 = el("label", "crow");
    r2.append(free);
    const act = requestAction(d.lines, d.comment, t, seat, v.stack.length);
    const send = button(act ? act.label : tr("play.req.send"), sendDraft, { cls: "on", disabled: off || !act,
      title: tr(act && act.kind === "pass" ? "play.req.send.pass"
        : !act && t.turn !== 0 && t.waiting_on !== seat ? "play.req.send.theirs" : "play.req.send.title") });
    const r3 = el("div", "crow");
    r3.append(send);
    const r4 = el("div", "crow");
    // 進める・終える: アイコンのボタン（押すと下書きに「その後」の行を足す）。位置は変えず、行けない所は押せない
    const r5 = el("div", "stepbtns");
    r5.append(...stepButtons(off, t.active === seat && t.turn > 0));
    r4.append(...[button(tr("play.req.undo"), undoLine, { disabled: !d.lines.length, title: tr("play.req.undo.title") }),
    button(tr("play.req.clear"), clearDraft, { disabled: !hasDraft() })].filter(Boolean));
    box.append(...[r2, r3, r5, r4].filter(Boolean));
    const sentLog = (ui.log || []).filter((e) => e.mine && e.request).slice(-3).reverse();
    if (sentLog.length) {
      const list = el("details", "reqsent");
      list.append(el("summary", null, tr("play.req.sent", { n: sentLog.length })));
      for (const e of sentLog) list.append(el("pre", "reqtext old", localize((e.texts || [e.label]).join("\n"))));
      box.append(list);
    }
    return box;
  }

  // 止める場所: 審判がステップを進めるとき、ここではあなたに番を回して止める（他は誘発・選択などがあるときだけ止まる）
  // 止める場所（非公開）: 卓の記録には載せず、本人と審判にだけ見える。設定が無い所では止めずに自動でパスされる
  let stops = null;          // 読み込んだ止める場所（Set）。null はまだ読んでいない
  let stopsLoading = false, stopsFailed = false;  // 読めなかったら、パネルでは小さな表の代わりにボタンを出す
  async function loadStops() {
    if (stopsLoading || stopsFailed || !ui.play) return;
    stopsLoading = true;
    try {
      stops = new Set((await source.getStops(ui.game, me())).stops || []);
    } catch (e) {
      stopsFailed = true;
      toast(tr("play.stops.loadFailed", { message: e.message }), true);
    } finally {
      stopsLoading = false;
    }
    renderPanel();
  }

  // 1マス = 1か所。自分・相手のターンの段 × ステップの列。今のステップの列に印をつける
  function stopGrid(set, { onToggle = null, compact = false } = {}) {
    const t = ui.view && ui.view.turn;
    const now = t && t.turn > 0 ? stepName(t) : null;
    const nowSide = t && (t.active === me() ? "own" : "opp");
    const grid = el("div", "stopgrid" + (compact ? " compact" : ""));
    grid.style.setProperty("--cols", STOP_STEPS.length);
    if (!compact) {
      grid.append(el("div", "sgcorner"));
      for (const g of STOP_GROUPS) {
        const h = el("div", "sggroup", tr(`play.group.${g.group}`));
        h.style.gridColumn = `span ${g.n}`;
        grid.append(h);
      }
      grid.append(el("div", "sgcorner"));
      for (const st of STOP_STEPS) grid.append(el("div", "sgstep" + (st === now ? " now" : ""), tr(`play.stopShort.${st}`)));
    }
    for (const side of ["own", "opp"]) {
      grid.append(el("div", "sgside", tr(`play.stops.${side}`)));
      for (const st of STOP_STEPS) {
        const code = `${side}:${st}`;
        const here = st === now && side === nowSide;
        if (side === "own" && st === "declare_blockers") { grid.append(el("div", "sgcell none" + (here ? " here" : ""))); continue; }
        const on = set.has(code);
        const cell = el(onToggle ? "button" : "div", "sgcell" + (on ? " on" : "") + (here ? " here" : "") + (st === now ? " nowcol" : ""));
        cell.title = tr(`play.stops.cell.${side}`, { step: stepLabel(st), on: on ? tr("play.stops.cell.on") : "", here: here ? tr("play.stops.cell.here") : "" });
        if (onToggle) {
          cell.type = "button";
          cell.setAttribute("aria-pressed", String(on));
          cell.setAttribute("aria-label", cell.title);
          cell.onclick = () => onToggle(code);
        }
        grid.append(cell);
      }
    }
    return grid;
  }

  async function editStops() {
    if (!stops) {
      try {
        stops = new Set((await source.getStops(ui.game, me())).stops || []);
      } catch (e) {
        return toast(tr("play.stops.loadFailed", { message: e.message }), true);
      }
    }
    const draft = new Set(stops);
    const dlg = $("ask");
    const form = el("form", "askform stopsform");
    form.method = "dialog";
    const body = el("div", "stopsbody");
    const draw = () => {
      const toggle = (code) => { draft.has(code) ? draft.delete(code) : draft.add(code); draw(); };
      const events = el("div", "sgevents");
      for (const code of ["opp:spell", "opp:attack", "opp:target"]) {
        const b = el("button", "sgevent" + (draft.has(code) ? " on" : ""), tr(`play.stops.ev.${code.slice(4)}`));
        b.type = "button";
        b.setAttribute("aria-pressed", String(draft.has(code)));
        b.onclick = () => toggle(code);
        events.append(b);
      }
      const presets = el("div", "sgpresets");
      presets.append(el("span", "muted", tr("play.stops.presets")));
      for (const [name, codes] of STOP_PRESETS) {
        const b = el("button", null, tr(`play.preset.${name}`));
        b.type = "button";
        b.onclick = () => { draft.clear(); codes.forEach((c) => draft.add(c)); draw(); };
        presets.append(b);
      }
      const wrap = el("div", "sgwrap");
      wrap.append(stopGrid(draft, { onToggle: toggle }));
      body.replaceChildren(
        el("div", "phead2", tr("play.stops.steps")), wrap,
        el("div", "phead2", tr("play.stops.events")), events, presets,
        el("div", "chelp muted", draft.size ? tr("play.stops.count", { n: draft.size }) : tr("play.stops.never")));
    };
    draw();
    let result = null;
    const buttons = el("div", "abuttons");
    const cancel = el("button", null, tr("play.cancel"));
    cancel.type = "button";
    cancel.onclick = () => dlg.close();
    const submit = el("button", "on", tr("play.save"));
    submit.type = "submit";
    form.onsubmit = () => { result = [...draft]; };
    buttons.append(cancel, submit);
    form.append(el("div", "atitle", tr("play.stops.title")), el("div", "chelp muted", tr("play.stops.help")),
      body, buttons);
    dlg.replaceChildren(form);
    dlg.onclose = async () => {
      if (!result) return;
      try {
        const r = await source.post(ui.game, "stops", { seat: me(), stops: result });
        stops = new Set(r.stops || result);
        stopsFailed = false;
        toast(stops.size ? tr("play.stops.saved", { n: stops.size }) : tr("play.stops.savedNone"));
      } catch (e) {
        toast(tr("play.stops.saveFailed", { message: e.message }), true);
      }
      renderPanel();
    };
    dlg.showModal();
    submit.focus();
  }

  // パネルの「いつでも」に出す、止める場所の小さな表（押すと編集）
  function stopsStrip() {
    if (!stops) { if (!stopsFailed) loadStops(); return null; }
    const box = el("button", "stopstrip");
    box.type = "button";
    box.title = tr(locked() ? "play.stops.strip.locked" : "play.stops.strip.title");
    box.disabled = locked();
    box.onclick = editStops;
    const ev = ["spell", "attack", "target"].filter((k) => stops.has(`opp:${k}`)).map((k) => tr(`play.stops.strip.${k}`));
    box.append(el("div", "sslabel", tr("play.stops.strip.label", {
      count: stops.size ? tr("play.stops.strip.n", { n: stops.size }) : tr("play.stops.strip.none"),
      events: ev.length ? tr("play.stops.strip.events", { list: ev.join("/") }) : "" })),
      stopGrid(stops, { compact: true }));
    return box;
  }

  async function declare() {
    const r = await ask({ title: tr("play.say.title"), fields: [{ name: "text", label: tr("play.field.text"), value: "" }] });
    if (r && r.text) declareKind("say", r.text);
  }

  function composeBox() {
    const box = el("div", "compose");
    if (pick) {
      box.append(el("div", "ctitle", pick.prompt), button(tr("play.cancel"), () => { pick = null; renderPanel(); }));
      return box;
    }
    const c = compose;
    box.append(el("div", "ctitle", c.kind === "target" ? tr("play.compose.targetsFor", { label: localize(c.label) })
      : tr(c.kind === "cast" ? "play.compose.cast" : "play.compose.activate", { card: nm(c.card || c.source) })));
    box.append(el("div", "chelp muted", tr("play.compose.help") + (c.kind === "target" ? "" : tr("play.compose.helpCost"))));
    const tg = el("div", "crow");
    tg.append(el("span", "muted", tr("play.compose.targets")));
    for (const t of c.targets) {
      const chip = el("button", "chip", `${nm(t)} ×`);
      chip.onclick = () => toggleTarget(t);
      tg.append(chip);
    }
    const row = el("label", "crow");
    const input = el("input");
    input.value = c.text;
    input.placeholder = tr("play.compose.placeholder");
    input.oninput = () => { c.text = input.value.trim(); };
    row.append(el("span", "muted", tr("play.compose.text")), input);
    box.append(tg, row);
    if (c.kind === "activate") {  // コストの {T}: 発生源をタップして起動する（盤面の予定でもタップ状態にする）
      const tap = el("label", "crow");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.checked = !!c.tap;
      box2.onchange = () => { c.tap = box2.checked; };
      tap.append(box2, el("span", null, tr("play.compose.tap", { card: nm(c.source), T: "{T}" })));
      box.append(tap);
    }
    const buttons = el("div", "crow");
    // 既定は「積んで解決」（積む行と解決する行）。スタックに積んだままにする（解決の前に何かする）のはオプション
    const buttons2 = c.kind === "target"
      ? [button(tr("play.compose.addTargets"), () => commit(), { cls: "on" })]
      : [button(tr(c.kind === "cast" ? "play.compose.castResolve" : "play.compose.activateResolve"), () => commit(true), { cls: "on",
          title: tr("play.compose.resolve.title") }),
        button(tr("play.compose.stackOnly"), () => commit(), { title: tr("play.compose.stackOnly.title") })];
    buttons.append(...buttons2, button(tr("play.cancel"), cancel));
    box.append(buttons);
    return box;
  }

  function renderPanel() {
    const panel = $("fl-play");
    if (!panel) return;
    panel.hidden = !ui.play;
    if (!ui.play || !ui.view) return;
    const v = ui.view, t = v.turn, seat = me();
    const mySent = ((v.requests || {}).pending || []).some((r) => r.player === seat);
    // 審判が処理した（本物の盤面になった）。送った後の再読込が別の更新と重なり、まだ依頼の前の盤面のことがあるので、
    // 依頼より後の盤面で確かめる
    if (sent && !mySent && ui.cursor > sentFrom) sent = null;
    const body = $("playbody");
    const wait = t.waiting_on;
    const waiting = ui.live && (busy || (!!wait && wait !== seat && !(ui.ai && ui.ai.status === "suspended")));
    const status = el("div", "pstatus" + (wait === seat ? " mine" : "") + (waiting ? " waiting" : ""));
    status.textContent = !ui.live ? tr(ui.playing ? "play.status.replaying" : "play.status.past")
      : busy ? tr("play.status.sending")
        : wait === seat ? tr("play.status.mine") + (myAsk(v, seat) ? tr("play.status.question") : t.priority === seat ? tr("play.status.priority") : "")
          : wait === "judge" ? tr("play.status.judge") : wait ? tr("play.status.waiting", { who: wait }) : tr("play.status.over");
    if (waiting) status.append(dots());
    $("playTitle").textContent = tr("play.panelTitle", { seat });
    const rows = [];
    const row = (...xs) => { const r = el("div", "prow"); r.append(...xs.filter(Boolean)); rows.push(r); };
    if (ui.live && ui.ai && ui.ai.status === "suspended") {  // 審判・AI の席が止まった（上限・API の失敗など）
      rows.push(el("div", "pask", tr("play.ai.stopped", { reason: ui.ai.reason })));
      row(button(tr("play.ai.resume"), async () => {
        try { await source.post(ui.game, "resume", { seat }); toast(tr("play.ai.resumed")); reload(); } catch (e) { toast(e.message, true); }
      }, { title: tr("play.ai.resume.title") }));
    }
    const idle = ui.live && idleState(ui.idle, wait, seat, Date.now());
    if (idle) {
      rows.push(el("div", "phint", tr("play.idle.notice", { who: idle.who, minutes: idle.minutes })));
      row(idle.canClaim
        ? button(tr("play.idle.claim"), async () => {
          if (!confirm(tr("play.idle.confirm", { who: idle.who }))) return;
          try { await source.post(ui.game, "timeout", { seat }); toast(tr("play.idle.done")); reload(); } catch (e) { toast(e.message, true); }
        }, { title: tr("play.idle.claim.title") })
        : el("span", "muted", tr("play.idle.left", { n: idle.left })));
    }
    if (locked()) { compose = null; pick = null; blocker = null; closeMenu(); }  // 審判の処理で盤面が変わる
    if (compose || pick) rows.push(composeBox());
    const mine = wait === seat && ui.live;
    if (mine) rows.push(el("div", "phead2", tr("play.yourMove")));
    const question = ui.live && myAsk(v, seat);
    // 質問が終わった（答えた・自由記述で答えた・審判が取り下げた）ら、自動で開いた束を閉じる
    if (autoOpened.zones.length && (!question || question.seq !== autoOpened.seq)) {
      closeAutoOpened();
      setTimeout(render);
    }
    for (const r of (v.requests || {}).pending || []) {  // 審判が処理中の依頼（本人の分だけ届く）
      const what = tr(`play.pending.${["answer", "mulligan"].includes(r.kind) ? r.kind : "request"}`);
      rows.push(el("div", "phead2", tr("play.pending.title", { player: r.player, what, seq: r.seq })),
        el("pre", "reqtext", localize(r.text) || what));
    }
    if (question) {
      // 審判の質問: 選択肢があればボタン、カードを選ぶ質問なら候補のカード、いつでも自由記述でも答えられる
      rows.push(el("div", "pask", tr("play.question", { seq: question.seq, text: localize(question.text) })));
      if ((question.cards || []).length) rows.push(cardAskBox(cardAsk()));
      row(...(question.choices || []).map((c) => button(c, () => answer(c))),
        button(tr("play.answerFree"), async () => {
          const r = await ask({ title: localize(question.text), fields: [{ name: "text", label: tr("play.answer.field"), value: "" }], ok: tr("play.answer.ok") });
          if (r && r.text) answer(r.text);
        }));
    } else if (mine && t.turn === 0 && !hasKept(v, seat)) {
      const need = (v.pregame || {}).to_bottom || 0;
      row(button(tr("play.keep"), () => declareKind("keep"),
        { title: need ? tr("play.keep.titleBottom", { n: need }) : tr("play.keep.title") }),
      button(tr("play.mulligan"), () => declareKind("mulligan"), { title: tr("play.mulligan.title") }));
      if (mulligans(v, seat)) rows.push(el("div", "phint muted", tr("play.mulligan.count", { n: mulligans(v, seat), need })));
    }
    // 攻撃されていてブロックを決める所: 盤面のカードを押して組む
    if (!blocking()) blocker = null;
    else if (!compose && !pick) rows.push(blockBox());
    // 途中で止まった自分の計画: 下書きが空なら戻し、あれば戻すかを選ぶ
    const back = !question && pendingResume();
    if (back && !hasDraft()) takeResume(back, true, false);
    else if (back) rows.push(resumeBox(back));
    // 依頼: 下書き（やること）を組み立てて審判に送る。パス・ターン終了もここから
    if (ui.live && !question && t.turn > 0) rows.push(requestBox());
    // 発言・設定: 番に関係なく行える操作と止める場所。使えない間（過去の盤面・送信中・審判の処理中・決着・投了した後）は出さない
    const self = v.players.find((p) => p.id === seat);
    const usable = ui.live && !busy && !locked() && !!wait && (!self || self.status === "playing");
    if (usable) {
      rows.push(el("div", "phead2 sep", tr("play.sayAndSettings")));
      const strip = stopsStrip();
      if (strip) rows.push(strip);
      row(button(tr("play.say"), declare), !strip && button(tr("play.stops.button"), editStops, { title: tr("play.stops.button.title") }),
        button(tr("play.concede"), async () => { if (confirm(tr("play.concede.confirm"))) declareKind("concede"); }));
    }
    body.replaceChildren(status, ...rows);
  }

  setInterval(() => { if (ui.play && ui.idle) renderPanel(); }, 30000);  // 時間切れの知らせの分を進める
  addEventListener("pointerdown", (e) => { if (!e.target.closest("#menu")) closeMenu(); }, true);
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMenu();
    if (e.key === "Escape" && (compose || pick) && !$("ask").open) cancel();
  });

  return {
    hooks: { card: onCard, drop: onDrop, mana: onMana, stack: onStack, player: onPlayer, pile: onPile, after: renderPanel, preview: previewView, retarget },
    renderPanel,
    reset() { compose = null; pick = null; blocker = null; stops = null; stopsFailed = false; draft = null; sent = null; shownView = null; Object.assign(chosen, { seq: null, cards: [] }); },
  };
}
