// GUI の対局: 席の Player として審判に依頼する（request_play_design）。人間も AI も卓（盤面）は動かさない。
// 土地を出す・唱える・攻撃する・1 枚引く…は、ブラウザの中の下書き（依頼の行）に足すだけで、送るまで盤面は変わらない。
// 「審判に依頼」で下書きを1つの依頼として審判に送り（「その後」は下書きの最後の行で決まる）、審判がルールに沿って卓に書く。
// 引く・見るなど隠れた情報の行は、手札・束に「？」の予定を出すだけ（中身は審判の処理の後に、席の view として届く）。
import { $, el } from "./dom.js";

// ---------------------------------------------------------------- 純粋な部品（node のテストでも使う）

export const STEPS = ["untap", "upkeep", "draw", "main1", "beginning_of_combat", "declare_attackers",
  "declare_blockers", "combat_damage", "end_of_combat", "main2", "end", "cleanup"];
export const STEP_JA = {
  untap: "アンタップ", upkeep: "アップキープ", draw: "ドロー", main1: "メイン1", beginning_of_combat: "戦闘開始",
  declare_attackers: "攻撃クリーチャー指定", declare_blockers: "ブロック・クリーチャー指定", combat_damage: "戦闘ダメージ",
  end_of_combat: "戦闘終了", main2: "メイン2", end: "終了", cleanup: "クリンナップ",
};

// 止める場所の列（play.py の STOP_STEPS と同じ順）。見出しはフェイズごとにまとめる
export const STOP_STEPS = ["upkeep", "draw", "main1", "beginning_of_combat", "declare_blockers", "combat_damage",
  "end_of_combat", "main2", "end"];
export const STOP_SHORT = {
  upkeep: "アップ", draw: "ドロー", main1: "メイン", beginning_of_combat: "開始", declare_blockers: "ブロック",
  combat_damage: "ダメージ", end_of_combat: "終了", main2: "メイン", end: "終了",
};
const STOP_GROUPS = [{ label: "開始", n: 2 }, { label: "第1", n: 1 }, { label: "戦闘", n: 4 }, { label: "第2", n: 1 }, { label: "最終", n: 1 }];
export const STOP_PRESETS = [
  ["おすすめ", ["own:main1", "own:main2", "opp:declare_blockers", "opp:end", "opp:spell", "opp:attack"]],
  ["全部", [...STOP_STEPS.flatMap((s) => ["own:" + s, "opp:" + s]).filter((c) => c !== "own:declare_blockers"), "opp:spell", "opp:attack"]],
  ["なし", []],
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
  { label: "開始", steps: ["upkeep", "draw"] }, { label: "第1", steps: ["main1"] },
  { label: "戦闘", steps: ["beginning_of_combat", "declare_attackers", "declare_blockers", "combat_damage", "end_of_combat"] },
  { label: "第2", steps: ["main2"] }, { label: "最終", steps: ["end"] },
];
// 「次へ」のボタンに出す行き先（枠の見出しが無いので、どのフェイズか分かる名前）
const NEXT_SHORT = {
  upkeep: "アップキープ", draw: "ドロー", main1: "メイン1", beginning_of_combat: "戦闘開始", declare_attackers: "攻撃",
  declare_blockers: "ブロック", combat_damage: "ダメージ", end_of_combat: "戦闘終了", main2: "メイン2", end: "終了ステップ",
};

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

// op を短い日本語の文に（下書きの一覧とラベル用）
export function describeOp(op, nm = (id) => id) {
  const ref = (r) => (Array.isArray(r) ? r.map(ref).join(", ")
    : r && typeof r === "object" ? JSON.stringify(r) : nm(r));
  const to = { graveyard: "墓地", hand: "手札", library: "ライブラリー", battlefield: "戦場", exile: "追放" };
  switch (op.op) {
    case "move": return `${ref(op.card)} を${to[op.to] || op.to}へ${op.position === "bottom" ? "（一番下）" : ""}`;
    case "damage": return `${ref(op.target)} に ${op.amount} 点のダメージ`;
    case "life_loss": return `${op.player ? ref(op.player) + " が" : ""}ライフを ${op.amount} 失う`;
    case "life_gain": return `${op.player ? ref(op.player) + " が" : ""}ライフを ${op.amount} 得る`;
    case "tap": return `${ref(op.card)} をタップ`;
    case "untap": return `${ref(op.card)} をアンタップ`;
    case "attack": return `${ref(op.attacker)} で ${ref(op.target)} を攻撃`;
    case "block": return `${ref(op.blocker)} で ${ref(op.attacker)} をブロック`;
    case "counter_add": return `${ref(op.target)} に ${op.kind} カウンター ×${op.amount || 1}`;
    case "counter_remove": return `${ref(op.target)} から ${op.kind} カウンター ×${op.amount || 1}`;
    case "note_add": return `${ref(op.target)} に Note「${op.text}」`;
    case "step": return `${STEP_JA[op.to] || op.to} へ`;
    case "stack_remove": return op.card_to === "graveyard" && op.item ? `${ref(op.item)} を打ち消す` : "解決を終える";
    case "draw": return `${op.count || 1} 枚引く`;
    case "declare": return `宣言: ${op.kind}${op.text ? " " + op.text : ""}`;
    case "create": return `トークン ${op.name || ""} を作る`;
    default: return `${op.op} ${JSON.stringify(Object.fromEntries(Object.entries(op).filter(([k]) => k !== "op")))}`;
  }
}

// ---------------------------------------------------------------- 下書き（純粋な部品）

// 依頼の「その後」（play.py の THEN_JA と同じ）。ステップ名なら、そのステップまで進める
export const THEN_JA = { continue: "続ける（まだ自分の番）", pass: "パス（相手に渡す）",
  resolve: "解決まで（相手が対応しなければ、積んだものを解決して自分の番を続ける）", end_turn: "ターン終了",
  turn_start: "自分のターンを始める" };
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
  return o;
}

// 「その後」の短い名前（送るボタン・下書きの欄に出す）
export function thenLabel(then) {
  return { continue: "続ける", pass: "パス", resolve: "解決まで", end_turn: "ターン終了", turn_start: "ターン開始" }[then]
    || (STEP_JA[then] ? `${STEP_JA[then]}へ` : then);
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
  if (t === "end_turn" || t === "pass") lines.push({ kind: "then", then: t, text: t === "pass" ? "パス（相手に渡す）" : "ターン終了" });
  else if (STEP_THEN.includes(t)) lines.push({ kind: "step", to: t, text: `${STEP_JA[t]} へ進む` });
  return lines;
}

// 「審判に依頼」のボタンが今することと、その名前。下書きが空でパスになるなら、審判を通さずにパスの宣言にする
// （自分のターンでも、相手の呪文・能力がスタックにあれば）。自分のターンで下書きもスタックも空なら、次のステップへ。
// 何も送れなければ null（相手のターンで優先権が無い）
export function requestAction(lines, comment, turn, seat, stackSize) {
  const r = toRequest(lines, turn, seat);
  const empty = !r.plan.length && !comment;
  if (empty && (r.then === "pass" || (r.then === "continue" && stackSize > 0))) {
    return turn.priority === seat ? { kind: "pass", label: "パス" } : null;
  }
  if (empty && r.then === "continue") {
    // 自分のターンで下書きが空: 次のステップへ進める（終了ステップなら、ターンを終える）。何もせずに止まらないように
    const next = nextStep(turn);
    r.then = STEP_THEN.includes(next) ? next : "end_turn";
  }
  return { kind: "request", ...r, label: `審判に依頼（その後: ${thenLabel(r.then)}）` };
}

// 下書き・送った依頼の行から、画面に出す予定: 手札に足す「？」の枚数と、束に付ける印、ブロックの予定（戦闘の欄）。
// 中身はまだ誰も知らない（審判が処理した後に、席の view として初めて届く）
export function planned(lines, seat) {
  const out = { hand: {}, piles: {}, blocks: [] };
  const note = (key, text) => { (out.piles[key] = out.piles[key] || []).push(text); };
  const lib = `${seat}.library`;
  for (const l of lines) {
    const n = l.count || 1;
    if (l.kind === "draw") { out.hand[seat] = (out.hand[seat] || 0) + n; note(lib, `−${n} 引く`); }
    else if (l.kind === "look") note(lib, `上 ${n} 枚を見る`);
    else if (l.kind === "reveal") note(lib, `上 ${n} 枚を公開`);
    else if (l.kind === "shuffle") note(lib, "シャッフル");
    else if (l.kind === "search") note(lib, "探す");
    else if (l.kind === "mill") note(`${(l.targets || [])[0] || seat}.library`, `−${n} 墓地へ`);
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

// 起動の行で、発生源をタップする（コストの {T}）と書いたもの
export const TAP_RE = /をタップして/;
// メニューの「タップ」「アンタップ」で足す行（<X> (#c1) をタップする／アンタップする）
const UNTAP_RE = /をアンタップする/;
const TAP_LINE_RE = /をタップする/;

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
      if (TAP_RE.test(l.text) && bf(id)) bf(id).tapped = true;
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
      if (!/タップしない/.test(l.text)) bf(id).tapped = true;
      out.combat.attacks.push({ attacker: id, target: (l.targets || [])[0], planned: true, line: i });
    } else if (l.kind === "other" && id && bf(id) && UNTAP_RE.test(l.text)) {
      bf(id).tapped = false;
    } else if (l.kind === "other" && id && bf(id) && TAP_LINE_RE.test(l.text)) {
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


// 小さなメニュー。items: [{label, fn, hint?}] と "-"（区切り）
function openMenu(ev, title, items) {
  closeMenu();
  const box = $("menu");
  box.replaceChildren();
  if (title) box.append(el("div", "mtitle", title));
  for (const it of items) {
    if (it === "-") { box.append(el("div", "msep")); continue; }
    if (!it) continue;
    const b = el("button", "mitem", it.label);
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
    const cancel = el("button", null, "やめる");
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

let toastTimer = null;
function toast(text, error) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, error ? 6000 : 2500);
}

// ---------------------------------------------------------------- 操作

export function createPlay(ui, { source, reload, showCard, toggleOpen, render }) {
  let compose = null;  // 組み立て中の行（唱える・起動・対象を選ぶ）
  let pick = null;     // 次のクリックで選ぶもの（ブロック先の攻撃クリーチャーなど）
  let blocker = null;  // ブロックを決める所で選んだ自分のクリーチャー（次に押した攻撃クリーチャーをブロックする）
  let busy = false;
  let draft = null;    // 下書き {key, lines, comment}。送るまでブラウザの中だけ（localStorage に対局・席ごと）
  let sent = null;     // 送った依頼の行。審判が処理するまで、印と「？」を出したままにする
  let sentFrom = 0;    // 送る前の cursor。盤面がこれより先に進んでから（依頼が届いた盤面で）処理済みかを見る
  const chosen = { seq: null, cards: [] };  // カードを選ぶ審判の質問で、今選んでいるカード
  // 盤面のカードの印は、下書き・組み立て中の行・カードを選ぶ質問から毎回作る（描画のたびに読む）
  Object.defineProperty(ui, "marks", { get: () => marks(), set() {}, configurable: true });
  // 下書きの「？」（手札に足す枚数・束の印）。render が読む
  Object.defineProperty(ui, "planned", { get: () => plannedNow(), set() {}, configurable: true });

  const me = () => ui.play.seat;
  const opp = () => (ui.view.players.find((p) => p.id !== me()) || {}).id;
  const nm = (id) => (ui.names[id] ? `<${ui.names[id]}>` : id);
  const ref = (id) => (ui.names[id] ? `<${ui.names[id]}> (${id})` : id);  // 依頼の文には名前と id を書く
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

  function line(kind, text, { cards = [], targets = [], count = 0, to = null } = {}) {
    return cleanLine({ kind, text, cards: cards.filter(isCard), targets, count, to });
  }

  function addLine(l) {
    if (!ui.play || !ui.view) return;
    if (!ui.live) return toast("最新の盤面（Live）に戻ってから操作する", true);
    if (judging()) return toast("審判が処理している間は組み立てない", true);
    if (ui.view.turn.turn === 0) return toast("ゲーム前はキープ・マリガンだけ", true);
    const lines = loadDraft().lines;
    // 「その後」の行（ターン終了・パス）は最後に1つだけ: 足し直せば入れ替え、他の行はその前に入れる
    const end = lines.length && lines[lines.length - 1].kind === "then";
    if (end && l.kind === "then") lines[lines.length - 1] = l;
    else if (end) lines.splice(lines.length - 1, 0, l);
    else lines.push(l);
    saveDraft();
    refresh();
  }
  const other = (text, cards = [], targets = []) => addLine(line("other", text, { cards, targets }));

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
      .map(({ l, i }) => ({ label: `予定を外す: ${l.text.length > 28 ? l.text.slice(0, 27) + "…" : l.text}`,
        fn: () => removeLine(i), hint: "下書きのこの行を外す（盤面は送るまで変わらない）" }));
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
    if (add) toast(`途中で止まった計画 #${r.of} の残りを下書きに戻した（直してから送る）`);
    if (redraw) refresh();
  }
  function resumeBox(r) {
    const box = el("div", "compose");
    box.append(el("div", "ctitle", `途中で止まった計画 #${r.of} の残り（${r.lines.length} 行・その後: ${thenLabel(r.then)}）`),
      el("div", "chelp muted", r.unknown ? "審判がどこまで処理したか分からない。戻したら、済んだ行を外す"
        : "相手の割り込みなどで止まった。下書きに戻して直してから送る"));
    const row = el("div", "crow");
    row.append(button("下書きに足す", () => takeResume(r), { cls: "on" }), button("捨てる", () => takeResume(r, false)));
    box.append(row);
    return box;
  }

  // ---- 送信（依頼・宣言・回答だけ。卓の op は書かない）

  async function post(what, body, okText) {
    if (!ui.play) return false;
    if (!ui.live) { toast("最新の盤面（Live）に戻ってから操作する", true); return false; }
    if (busy) return false;
    busy = true;
    renderPanel();
    let ok = false;
    try {
      const r = await source.post(ui.game, what, { ...body, seat: me(), expect: ui.cursor });
      const stopped = r.result.stopped;
      if (stopped) toast(`送れなかった（${stopped.reason}）: ${stopped.error || ""}`, true);
      else if (okText) toast(okText);
      ok = !stopped;
    } catch (e) {
      toast(e.stale ? "盤面が先に進んでいた。最新にしたので、下書きを確かめて送り直す" : `送れない: ${e.message}`, true);
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
    if (!act) return toast("やることか、進める先（進めるボタン）を足す", true);
    if (act.kind === "pass") return declareKind("pass");
    const lines = act.plan;
    const from = ui.cursor;
    blocker = null;
    if (await post("request", { plan: lines, then: act.then, comment: d.comment }, "審判に依頼した")) {
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
    else return toast(`選べるのは ${q.pick[1]} 枚まで`, true);
    refresh();
  }

  function answerCards(q, cards) {
    return answer(cards.length ? cards.map(nm).join("、") : "選ばない", cards);
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
    if (judging()) return toast("審判が処理している間は組み立てない", true);
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
    const extra = c.text ? `（${c.text}）` : "";
    if (c.kind === "target") {
      if (!c.targets.length) return toast("対象を押して選ぶ", true);
      compose = null;
      return addLine(line("target", `${c.label} の対象を ${c.targets.map(ref).join(", ")} にする${extra}`, { targets: c.targets }));
    }
    const src = c.card || c.source;
    const aim = c.targets.length ? ` 対象: ${c.targets.map(ref).join(", ")}` : "";
    const verb = c.kind === "cast" ? "を唱える" : c.tap ? "をタップして能力を起動する" : "の能力を起動する";
    compose = null;
    addLine(line(c.kind, `${ref(src)} ${verb}${aim}${extra}`, { cards: [src], targets: c.targets }));
    if (resolve === true) addLine(line("resolve", `${c.kind === "cast" ? `${ref(src)} を` : `${ref(src)} の能力を`}解決する`, { cards: [src] }));
  }

  // ---- メニューの中身（どれも下書きに行を足すだけ）

  function moveItems(id, zone) {
    const mv = (word) => () => other(`${ref(id)} を${word}`, [id]);
    return [
      zone !== "battlefield" && { label: "戦場に出す", fn: mv("戦場に出す") },
      !zone.endsWith(".hand") && { label: "手札に戻す（オーナーの）", fn: mv("オーナーの手札に戻す") },
      !zone.endsWith(".graveyard") && { label: zone === "battlefield" ? "墓地へ（生け贄・破壊）" : "墓地へ", fn: mv("墓地に置く") },
      zone !== "exile" && { label: "追放する", fn: mv("追放する") },
      { label: "ライブラリーの一番上へ", fn: mv("ライブラリーの一番上に置く") },
      { label: "ライブラリーの一番下へ", fn: mv("ライブラリーの一番下に置く") },
    ];
  }

  async function counter(target, sign) {
    const r = await ask({ title: `${nm(target)} のカウンター`, fields: [
      { name: "kind", label: "種類", value: "+1/+1" }, { name: "amount", label: "数", value: 1, type: "number" }] });
    if (!r || !r.kind || !r.amount) return;
    other(sign > 0 ? `${ref(target)} に ${r.kind} カウンターを ${r.amount} 個置く` : `${ref(target)} から ${r.kind} カウンターを ${r.amount} 個取り除く`,
      [target], isCard(target) ? [] : [target]);
  }

  async function note(target) {
    const r = await ask({ title: `${nm(target)} に Note`, fields: [
      { name: "text", label: "内容", value: "" }, { name: "eot", label: "ターン終了まで", type: "checkbox" }] });
    if (!r || !r.text) return;
    other(`${ref(target)} に Note「${r.text}」を付ける${r.eot ? "（ターン終了まで）" : ""}`, [target]);
  }

  async function amount(title, fn, value = 1) {
    const r = await ask({ title, fields: [{ name: "n", label: "数", value, type: "number" }] });
    if (r && r.n > 0) fn(Math.min(99, Math.floor(r.n)));
  }

  // 攻撃する: まず相手の Player を攻撃する行を足す。プレインズウォーカー・バトルへは、戦闘の欄で攻撃先を変える（retarget）
  const attackText = (id, target, tap) => `${ref(id)} で ${ref(target)} を攻撃する${tap ? "" : "（タップしない）"}`;
  function attackItems(c) {
    const t = ui.view.turn;
    if (t.active !== me() || c.land) return [];
    const attack = (tap) => () => addLine(line("attack", attackText(c.id, opp(), tap), { cards: [c.id], targets: [opp()] }));
    return [
      { label: "攻撃する", fn: attack(true) },
      { label: "攻撃する（タップしない・警戒）", fn: attack(false) },
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
      const to = await ask({ title: `${nm(a.attacker)} の攻撃先`, choices: targets.map((id) =>
        ({ label: `${id === a.target ? "✓ " : ""}${attackLabel(ui.view, id)}`, value: id })) });
      if (!to || to === a.target || loadDraft().lines[i] !== l) return;
      loadDraft().lines[i] = line("attack", attackText(a.attacker, to, !/タップしない/.test(l.text)), { cards: [a.attacker], targets: [to] });
      saveDraft();
      refresh();
    };
  }

  function attackLabel(v, id) {
    const c = (v.zones.battlefield.cards || []).find((x) => x.id === id);
    if (!c) return `${id}（Player）`;
    const n = (c.counters || {})[c.attackable === "battle" ? "defense" : "loyalty"];
    return `${nm(id)}（${c.attackable === "battle" ? "バトル" : "プレインズウォーカー"}${n !== undefined ? ` ${n}` : ""}）`;
  }

  function blockItems(c) {
    const v = ui.view;
    if (v.turn.active === me() || !v.combat.attacks.length || c.land) return [];
    return [{ label: "ブロックする…（攻撃クリーチャーを選ぶ）", fn: () => {
      const attackers = v.combat.attacks.map((a) => a.attacker);
      const doBlock = (attacker) => addLine(line("block", `${ref(c.id)} で ${ref(attacker)} をブロックする`,
        { cards: [c.id], targets: [attacker] }));
      if (attackers.length === 1) return doBlock(attackers[0]);
      pick = { prompt: `${nm(c.id)} がブロックする攻撃クリーチャーを選ぶ`, accept: (card) => {
        if (!attackers.includes(card.id)) { toast("攻撃しているクリーチャーを選ぶ", true); return; }
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
      items.push((!kind || kind.land) && { label: "土地として出す", fn: () => addLine(line("play_land", `${ref(id)} を出す`, { cards: [id] })) },
        (!kind || kind.spell) && { label: "唱える…", fn: () => start({ kind: "cast", card: id, targets: [], text: "" }) },
        { label: "公開する", fn: () => other(`${ref(id)} を公開する`, [id]) }, "-",
        { label: "捨てる", fn: () => other(`${ref(id)} を捨てる`, [id]) },
        ...moveItems(id, zone).filter((x) => x && !/墓地/.test(x.label)));
    } else if (zone === "battlefield") {
      if (mine) {
        items.push(c.tapped ? { label: "アンタップ", fn: () => other(`${ref(id)} をアンタップする`, [id]) }
          : { label: "タップ", fn: () => other(`${ref(id)} をタップする`, [id]) });
        items.push({ label: "能力を起動…", fn: () => start({ kind: "activate", source: id, targets: [], text: "" }) });
        items.push(...attackItems(c), ...blockItems(c));
      } else {
        items.push(c.tapped ? { label: "アンタップ", fn: () => other(`${ref(id)} をアンタップする`, [id]) }
          : { label: "タップ（効果で）", fn: () => other(`${ref(id)} をタップする（効果で）`, [id]) },
        { label: "ダメージを与える…", fn: () => amount(`${nm(id)} へのダメージ`, (n) => other(`${ref(id)} に ${n} 点のダメージ`, [id])) },
        { label: "コントロールを得る", fn: () => other(`${ref(id)} のコントロールを得る`, [id]) });
      }
      const inCombat = ui.view.combat.attacks.some((a) => a.attacker === id) || ui.view.combat.blocks.some((b) => b.blocker === id);
      if (inCombat) items.push({ label: "戦闘から取り除く", fn: () => other(`${ref(id)} を戦闘から取り除く`, [id]) });
      items.push("-", { label: "カウンターを置く…", fn: () => counter(id, 1) });
      if (c.counters && Object.keys(c.counters).length) items.push({ label: "カウンターを取り除く…", fn: () => counter(id, -1) });
      items.push({ label: "Note を付ける…", fn: () => note(id) });
      for (const n of c.notes || []) {
        items.push({ label: `Note を外す: ${n.text.length > 24 ? n.text.slice(0, 23) + "…" : n.text}`, fn: () => other(`${ref(id)} の Note「${n.text}」を外す`, [id]) });
      }
      items.push("-", ...moveItems(id, zone));
      if (c.token) items.push({ label: "取り除く（トークンの消滅）", fn: () => other(`トークン ${ref(id)} を取り除く`, [id]) });
    } else if (zone && zone.endsWith(".library")) {
      items.push(...moveItems(id, zone));
    } else if (zone === "stack") {
      const i = ui.view.stack.findIndex((s) => s.card === id);
      if (i >= 0) return stackItems(ui.view.stack[i], i);
    } else if (zone) {
      if (c.owner === me()) items.push({ label: "唱える…", fn: () => start({ kind: "cast", card: id, targets: [], text: "" }) });
      items.push(...moveItems(id, zone));
    }
    items.push("-", { label: "詳細", fn: () => showCard(c) });
    return items;
  }

  function stackItems(s, i) {
    const items = [];
    const label = s.card ? ref(s.card) : `${ref(s.source)} の能力`;
    if (i === 0) {
      items.push({ label: "解決する", fn: () => addLine(line("resolve", `スタックの一番上の ${label} を解決する`, { targets: [s.id] })),
        hint: "解決を依頼する（効果・誘発は審判が処理する）" });
    }
    if (s.controller === me() && !s.card) {
      items.push({ label: "対象を選ぶ…", fn: () => start({ kind: "target", item: s.id, label, targets: [], text: "" }) });
    }
    const c = s.card ? findCard(ui.view, s.card) : s.source && findCard(ui.view, s.source);
    if (c) items.push("-", { label: "詳細", fn: () => showCard(c) });
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
      if (!blocker) { toast("先に、ブロックする自分のクリーチャーを押す", true); return true; }
      const attacker = ids.find((id) => attackers.includes(id));
      const b = blocker;
      blocker = null;
      addLine(line("block", `${ref(b)} で ${ref(attacker)} をブロックする`, { cards: [b], targets: [attacker] }));
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
    if (c.tapped) { toast("タップ状態のクリーチャーはブロックできない", true); return true; }
    blocker = blocker === free ? null : free;
    refresh();
    return true;
  }

  function blockBox() {
    const box = el("div", "compose blockbox");
    box.append(el("div", "ctitle", "ブロック指定"));
    box.append(el("div", "chelp muted", blocker
      ? `${nm(blocker)} がブロックする攻撃クリーチャーを押す（もう一度押すと選び直し）`
      : "自分のクリーチャー → 攻撃クリーチャーの順に押す（予定のクリーチャーを押すと外す）"));
    const lines = blockLines();
    if (lines.length) {
      const list = el("ol", "cops reqlines");
      for (const { l, i } of lines) {
        const li = el("li", null, `${nm(l.cards[0])} → ${nm(l.targets[0])}`);
        li.title = l.text;
        const x = el("button", "chip", "×");
        x.title = "このブロックを外す";
        x.onclick = () => removeLine(i);
        li.append(x);
        list.append(li);
      }
      box.append(list);
    }
    const row = el("div", "crow");
    const none = loadDraft().lines.some((l) => l.kind === "other" && l.text === "ブロックしない");
    box.append(el("div", "chelp muted", "決めたら、下の「審判に依頼」で送る"));
    row.append(button("ブロックしない", () => { blocker = null; other("ブロックしない"); },
      { disabled: lines.length > 0 || none, title: "ブロックしないことを下書きに足す" }));
    if (blocker) row.append(button("選び直す", () => { blocker = null; refresh(); }));
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
    const title = `${card.name ? `<${card.name}>` : "カード"} ${card.id}`;
    if (card.planned) {  // 下書き・送った依頼で仮に出したカード: 予定を外す・続きの予定を足す（本物になるのは審判の処理の後）
      const items = unplanItems(card.id);
      const next = zone === "battlefield" && (card.controller || card.owner) === me() ? [
        { label: "能力を起動…", fn: () => start({ kind: "activate", source: card.id, targets: [], text: "", tap: false }),
          hint: "出す予定のパーマネントの能力を、その後に起動する予定にする" },
        card.tapped ? { label: "アンタップ", fn: () => other(`${ref(card.id)} をアンタップする`, [card.id]) }
          : { label: "タップ", fn: () => other(`${ref(card.id)} をタップする`, [card.id]) },
        "-"] : [];
      openMenu(ev, title + "（予定）", [...next,
        ...(items.length ? items : [{ label: "送った依頼の予定（審判の処理を待つ）", fn: () => {} }]),
        "-", { label: "詳細", fn: () => showCard(c) }]);
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
    const mv = (word) => ({ label: `${nm(id)} を${word}`, run: () => other(`${ref(id)} を${word}`, [id]) });
    if (place === "battlefield") {
      const kind = (ui.view.card_kinds || {})[id] || {};  // 土地の面があれば土地として出す（唱えるならメニューから）
      if (kind.land) return { label: `${nm(id)} を土地として出す`, run: () => addLine(line("play_land", `${ref(id)} を出す`, { cards: [id] })) };
      return { label: `${nm(id)} を唱えて解決する`, run: () => {
        addLine(line("cast", `${ref(id)} を唱える`, { cards: [id] }));
        addLine(line("resolve", `${ref(id)} を解決する`, { cards: [id] }));
      } };
    }
    if (place === "graveyard") return mv("捨てる");
    if (place === "exile") return mv("追放する");
    if (place === "library-top") return mv("ライブラリーの一番上に置く");
    if (place === "library-bottom") return mv("ライブラリーの一番下に置く");
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
      const what = s.card ? `${ref(s.card)} を` : `${ref(s.source)} の能力を`;
      // 一番上なら、続けて解決する予定を足せる（呪文はカード、能力は発生源で、どの項目かを審判に伝える）
      const resolve = i === 0 ? [{ label: "解決する", fn: () => addLine(line("resolve", `${what}解決する`, { cards: [s.card || s.source] })),
        hint: "積む予定の項目を、続けて解決する予定にする（効果・誘発は審判が処理する）" }, "-"] : [];
      openMenu(ev, `${s.card ? nm(s.card) : `${nm(s.source)} の能力`}（予定）`, [...resolve, ...(at >= 0
        ? [{ label: "この予定を外す", fn: () => removeLine(at) }] : [{ label: "送った依頼の予定（審判の処理を待つ）", fn: () => {} }])]);
      return true;
    }
    openMenu(ev, s.card ? nm(s.card) : `${nm(s.source)} の能力`, stackItems(s, i));
    return true;
  }

  function onPlayer(pid, ev) {
    if (!ui.play || !ui.live || locked()) return;
    if (compose) return toggleTarget(pid);
    const self = pid === me();
    const life = (word) => () => amount(`${pid} が${word}`, (n) => other(`${pid} が${word.replace("…", "")} ${n}`, [], [pid]));
    openMenu(ev, pid, [
      self && { label: "ライフ −1", fn: () => other(`${pid} のライフ −1`, [], [pid]) },
      self && { label: "ライフ ＋1", fn: () => other(`${pid} のライフ +1`, [], [pid]) },
      { label: "ダメージを与える…", fn: () => amount(`${pid} へのダメージ`, (n) => other(`${pid} に ${n} 点のダメージ`, [], [pid])) },
      { label: "ライフを失う…", fn: life("ライフを失う") },
      { label: "ライフを得る…", fn: life("ライフを得る") },
      { label: "カウンターを置く…", fn: () => counter(pid, 1) },
    ]);
  }

  // 浮いているマナ（自分のマナ・プール）を押したとき: 使う予定を下書きに足す（何に使うかは文で補う）。
  // 行の targets にマナの id を書くので、下書きの写しでもその分が減って見える
  function onMana(pid, m, ev) {
    if (!ui.play || !ui.live || locked() || pid !== me() || ui.view.turn.turn === 0) return false;
    const sym = `{${m.color}}`;
    const use = (n) => addLine(line("other", `マナ・プールの ${sym}（${m.id}）を ${n} 使う`, { targets: [m.id], count: n }));
    const pool = (shownView || ui.view).players.find((p) => p.id === pid).mana;
    openMenu(ev, `マナ ${sym} ×${m.amount}`, [
      { label: `${sym} を 1 使う`, fn: () => use(1) },
      m.amount > 1 && { label: `${sym} を使う…（数）`, fn: () => amount(`${sym} を使う数`, (n) => use(Math.min(n, m.amount)), m.amount) },
      m.amount > 1 && { label: `${sym} を全部（${m.amount}）使う`, fn: () => use(m.amount) },
      "-",
      { label: "マナ・プールを空にする", fn: () => addLine(line("other", "マナ・プールを空にする", { targets: pool.map((x) => x.id) })),
        hint: "浮いているマナを全部失う" },
    ]);
    return true;
  }

  // 束: 自分のライブラリーから引く・見る・公開・シャッフルは、手札・束に「？」の予定を出すだけ（中身は審判の処理の後）
  function onPile(key, ev) {
    if (!ui.play || !ui.live) return false;
    const [pid, zone] = key.split(".");
    const open = { label: ui.open.has(key) ? "中身を閉じる" : "中身を開く（知っているカード）", fn: () => toggleOpen(key) };
    if (zone !== "library") return false;
    const items = [];
    if (ui.view.turn.turn === 0 || locked()) {  // ゲーム前・審判を待っている間は中身を見るだけ  // ゲーム前はキープ・マリガンだけ（引き直しは審判が行う）
      openMenu(ev, `${pid} のライブラリー`, [open]);
      return true;
    }
    if (pid === me()) {
      items.push({ label: "1 枚引く", fn: () => addLine(line("draw", "1 枚引く", { count: 1 })) },
        { label: "N 枚引く…", fn: () => amount("引く枚数", (n) => addLine(line("draw", `${n} 枚引く`, { count: n })), 2) },
        { label: "上から N 枚を見る…", fn: () => amount("見る枚数", (n) => addLine(line("look", `ライブラリーの上から ${n} 枚を見る`, { count: n }))) },
        { label: "一番上を公開する", fn: () => addLine(line("reveal", "ライブラリーの一番上を公開する", { count: 1 })) },
        { label: "シャッフルする", fn: () => addLine(line("shuffle", "ライブラリーをシャッフルする")) });
    }
    items.push({ label: "上から N 枚を墓地へ（切削）…", fn: () => amount("墓地へ置く枚数", (n) => addLine(
      line("mill", `${pid} のライブラリーの上から ${n} 枚を墓地に置く`, { count: n, targets: [pid] }))) },
    "-", open);
    openMenu(ev, `${pid} のライブラリー`, items);
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
    box.append(el("div", "chelp muted", `${lo === hi ? `${lo} 枚` : `${lo}〜${hi} 枚`}選ぶ（候補を押す。盤面のカードを押してもよい）`));
    // 候補が閉じた領域（墓地・追放など）にあれば開いて、盤面でも見えるようにする
    for (const id of q.cards) {
      const z = zoneOf(ui.view, id);
      if (z && z !== "battlefield" && z !== "stack" && !z.endsWith(".hand") && !ui.open.has(z)) { ui.open.add(z); setTimeout(render); }
    }
    const list = el("div", "crow");
    for (const id of q.cards) {
      const on = chosen.cards.includes(id);
      const b = button(nm(id), () => toggleChosen(q, id), { cls: "chip cand" + (on ? " on" : ""), title: `${id}（もう一度押すと外す）` });
      const c = findCard(ui.view, id);
      const info = button("詳細", () => showCard(c), { cls: "chip" });
      info.disabled = !c;
      list.append(b, info);
    }
    const ok = button(n ? `決定（${n} 枚）` : "決定", () => answerCards(q, chosen.cards),
      { cls: "on", disabled: n < lo || n > hi || !n });
    const none = lo === 0 && button("選ばない", () => answerCards(q, []));
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
    const go = (s) => () => addLine(line("step", `${STEP_JA[s]} へ進む`, { to: s }));
    const then = (k, text) => () => addLine({ kind: "then", then: k, text });
    const next = mine ? STEPS.slice(i + 1).find((s) => STEP_THEN.includes(s)) : null;
    const phase = STEP_PHASE[stepName(t)];
    const grid = el("div", "stepgrid");
    for (const group of STEP_GROUPS) {
      const g = el("div", "stepgroup");
      g.style.flexGrow = group.steps.length;
      if (mine && group.steps.some((s) => STEP_PHASE[s] === phase)) g.classList.add("curphase");
      g.append(el("div", "sglabel", group.label));
      const btns = el("div", "sgbtns");
      for (const s of group.steps) {
        const cur = mine && STEPS.indexOf(s) === i;
        const ok = mine && STEPS.indexOf(s) > i;
        btns.append(iconButton(ICON[s], `${STEP_JA[s]} へ進む`, go(s),
          { disabled: off || !ok, cls: cur ? "cur" : mine && !ok ? "past" : null,
            title: cur ? `${STEP_JA[s]}（今ここ）` : `${STEP_JA[s]} へ進む` }));
      }
      g.append(btns);
      grid.append(g);
    }
    const acts = el("div", "stepacts");
    acts.append(iconButton(ICON.next, next ? `次のステップ（${STEP_JA[next]}）へ進む` : "次のステップへ進む",
        next ? go(next) : null, { disabled: off || !next, text: next ? `次へ: ${NEXT_SHORT[next]}` : "次へ" }),
      iconButton(ICON.endTurn, "ターン終了", then("end_turn", "ターン終了"),
        { disabled: off || !mine, text: "ターン終了", title: "ターン終了（終了ステップ・クリンナップは審判が処理する）" }),
      iconButton(ICON.pass, "パス（相手に渡す）", then("pass", "パス（相手に渡す）"),
        { disabled: off || !mine, text: "パス", title: "パス（最後に優先権を相手に渡す）" }));
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
    box.append(el("div", "phead2", "審判への依頼（下書き）"));
    if (d.lines.length) {
      const list = el("ol", "cops reqlines");
      d.lines.forEach((l, i) => {
        const bad = staleLine(l);
        const li = el("li", bad ? "stale" : null, l.text);
        if (bad) li.title = "盤面が変わり、このカードが思った所に無い。外すか、足し直す";
        const x = el("button", "chip", "×");
        x.title = "この行を外す";
        x.disabled = locked();
        x.onclick = () => removeLine(i);
        li.append(x);
        list.append(li);
      });
      box.append(list);
    } else {
      box.append(el("div", "chelp muted", "カード・束・Player・スタックを押して、やることを足す（送るまで盤面は変わらない）"));
    }
    const off = judging() || asked();
    const free = el("input");
    free.placeholder = "やること・補足を書いて Enter で1行足す（例: 対象は #c12、X=2）";
    free.title = "ボタンに無い操作や、対象・モード・X などの補足。Enter で下書きに1行足す";
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
    const send = button(act ? act.label : "審判に依頼", sendDraft, { cls: "on", disabled: off || !act,
      title: act && act.kind === "pass" ? "優先権を相手に渡す（下書きが無いので審判は通さない）"
        : "下書きを審判に送る。「その後」は最後の行（進めるボタンで足す）で決まる。無ければ自分のターンは続ける・相手のターンはパス" });
    const r3 = el("div", "crow");
    r3.append(send);
    const r4 = el("div", "crow");
    // 進める・終える: アイコンのボタン（押すと下書きに「その後」の行を足す）。位置は変えず、行けない所は押せない
    const r5 = el("div", "stepbtns");
    r5.append(...stepButtons(off, t.active === seat && t.turn > 0));
    r4.append(...[button("直前の行を取り消す", undoLine, { disabled: !d.lines.length, title: "下書きの最後の行を外す（送る前なら何度でも）" }),
    button("全部捨てる", clearDraft, { disabled: !hasDraft() })].filter(Boolean));
    box.append(...[r2, r3, r5, r4].filter(Boolean));
    const sentLog = (ui.log || []).filter((e) => e.mine && e.request).slice(-3).reverse();
    if (sentLog.length) {
      const list = el("details", "reqsent");
      list.append(el("summary", null, `送った依頼（最近 ${sentLog.length} 件）`));
      for (const e of sentLog) list.append(el("pre", "reqtext old", (e.texts || [e.label]).join("\n")));
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
      toast(`止める場所を読めない: ${e.message}`, true);
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
        const h = el("div", "sggroup", g.label);
        h.style.gridColumn = `span ${g.n}`;
        grid.append(h);
      }
      grid.append(el("div", "sgcorner"));
      for (const st of STOP_STEPS) grid.append(el("div", "sgstep" + (st === now ? " now" : ""), STOP_SHORT[st]));
    }
    for (const side of ["own", "opp"]) {
      grid.append(el("div", "sgside", side === "own" ? "自分" : "相手"));
      for (const st of STOP_STEPS) {
        const code = `${side}:${st}`;
        const here = st === now && side === nowSide;
        if (side === "own" && st === "declare_blockers") { grid.append(el("div", "sgcell none" + (here ? " here" : ""))); continue; }
        const on = set.has(code);
        const cell = el(onToggle ? "button" : "div", "sgcell" + (on ? " on" : "") + (here ? " here" : "") + (st === now ? " nowcol" : ""));
        cell.title = `${side === "own" ? "自分" : "相手"}のターン: ${STEP_JA[st]}${on ? "（止まる）" : ""}${here ? " ← 今ここ" : ""}`;
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
        return toast(`止める場所を読めない: ${e.message}`, true);
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
      for (const [code, label] of [["opp:spell", "相手が呪文・能力を積んだとき"], ["opp:attack", "相手が攻撃したとき"]]) {
        const b = el("button", "sgevent" + (draft.has(code) ? " on" : ""), label);
        b.type = "button";
        b.setAttribute("aria-pressed", String(draft.has(code)));
        b.onclick = () => toggle(code);
        events.append(b);
      }
      const presets = el("div", "sgpresets");
      presets.append(el("span", "muted", "まとめて: "));
      for (const [label, codes] of STOP_PRESETS) {
        const b = el("button", null, label);
        b.type = "button";
        b.onclick = () => { draft.clear(); codes.forEach((c) => draft.add(c)); draw(); };
        presets.append(b);
      }
      const wrap = el("div", "sgwrap");
      wrap.append(stopGrid(draft, { onToggle: toggle }));
      body.replaceChildren(
        el("div", "phead2", "ステップ（押して切り替え）"), wrap,
        el("div", "phead2", "相手の行動"), events, presets,
        el("div", "chelp muted", draft.size ? `${draft.size} か所で止まる。ここ以外の応答の機会は自動でパスされる`
          : "どこでも止まらない。誘発・選択・審判の質問があるときだけ番が来る"));
    };
    draw();
    let result = null;
    const buttons = el("div", "abuttons");
    const cancel = el("button", null, "やめる");
    cancel.type = "button";
    cancel.onclick = () => dlg.close();
    const submit = el("button", "on", "保存");
    submit.type = "submit";
    form.onsubmit = () => { result = [...draft]; };
    buttons.append(cancel, submit);
    form.append(el("div", "atitle", "止める場所"), el("div", "chelp muted", "相手には見えない。審判がステップを進めるとき、色のついた所で止めてあなたに番を回す"),
      body, buttons);
    dlg.replaceChildren(form);
    dlg.onclose = async () => {
      if (!result) return;
      try {
        const r = await source.post(ui.game, "stops", { seat: me(), stops: result });
        stops = new Set(r.stops || result);
        stopsFailed = false;
        toast(stops.size ? `止める場所: ${stops.size} か所` : "止める場所: なし");
      } catch (e) {
        toast(`保存できない: ${e.message}`, true);
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
    box.title = locked() ? "審判の処理が済んでから変える" : "止める場所を変える（相手には見えない）";
    box.disabled = locked();
    box.onclick = editStops;
    const ev = [stops.has("opp:spell") && "呪文", stops.has("opp:attack") && "攻撃"].filter(Boolean);
    box.append(el("div", "sslabel", `止める場所 ${stops.size ? `${stops.size} か所` : "なし"}${ev.length ? `・相手の${ev.join("/")}` : ""}`),
      stopGrid(stops, { compact: true }));
    return box;
  }

  async function declare() {
    const r = await ask({ title: "発言（相手と審判に見える）", fields: [{ name: "text", label: "内容", value: "" }] });
    if (r && r.text) declareKind("say", r.text);
  }

  function composeBox() {
    const box = el("div", "compose");
    if (pick) {
      box.append(el("div", "ctitle", pick.prompt), button("やめる", () => { pick = null; renderPanel(); }));
      return box;
    }
    const c = compose;
    box.append(el("div", "ctitle", c.kind === "target" ? `${c.label} の対象を選ぶ`
      : `${nm(c.card || c.source)} を${c.kind === "cast" ? "唱える" : "起動する"}`));
    box.append(el("div", "chelp muted", "対象: カード・Player・スタックを押す（もう一度押すと外す）" +
      (c.kind === "target" ? "" : "。支払い・コストは審判が処理する")));
    const tg = el("div", "crow");
    tg.append(el("span", "muted", "対象 "));
    for (const t of c.targets) {
      const chip = el("button", "chip", `${nm(t)} ×`);
      chip.onclick = () => toggleTarget(t);
      tg.append(chip);
    }
    const row = el("label", "crow");
    const input = el("input");
    input.value = c.text;
    input.placeholder = "モード・X・追加コストなど（任意）";
    input.oninput = () => { c.text = input.value.trim(); };
    row.append(el("span", "muted", "文 "), input);
    box.append(tg, row);
    if (c.kind === "activate") {  // コストの {T}: 発生源をタップして起動する（盤面の予定でもタップ状態にする）
      const tap = el("label", "crow");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.checked = !!c.tap;
      box2.onchange = () => { c.tap = box2.checked; };
      tap.append(box2, el("span", null, ` ${nm(c.source)} をタップする（{T}）`));
      box.append(tap);
    }
    const buttons = el("div", "crow");
    // 既定は「積んで解決」（積む行と解決する行）。スタックに積んだままにする（解決の前に何かする）のはオプション
    const buttons2 = c.kind === "target"
      ? [button("下書きに足す（対象）", () => commit(), { cls: "on" })]
      : [button(c.kind === "cast" ? "唱えて解決" : "起動して解決", () => commit(true), { cls: "on",
          title: "積む行と、続けて解決する行を下書きに足す（相手が対応しなければ審判が解決する）" }),
        button("スタックに積むだけ", () => commit(), { title: "積む行だけを足す（解決の前に、続けて何かを積むときなど）" })];
    buttons.append(...buttons2, button("やめる", cancel));
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
    const status = el("div", "pstatus" + (wait === seat ? " mine" : ""));
    status.textContent = !ui.live ? (ui.playing ? "再生中…（最後まで進むと操作できる。Live で今すぐ最新へ）" : "過去の盤面を表示中（Live で操作）")
      : busy ? "送信中…"
        : wait === seat ? "あなたの番" + (myAsk(v, seat) ? "（審判の質問）" : t.priority === seat ? "（優先権）" : "")
          : wait === "judge" ? "審判を待っています（処理が済むまで操作できない）" : wait ? `${wait} を待っています` : "決着";
    $("playTitle").textContent = `${seat} として操作`;
    const rows = [];
    const row = (...xs) => { const r = el("div", "prow"); r.append(...xs.filter(Boolean)); rows.push(r); };
    if (locked()) { compose = null; pick = null; blocker = null; closeMenu(); }  // 審判の処理で盤面が変わる
    if (compose || pick) rows.push(composeBox());
    const mine = wait === seat && ui.live;
    if (mine) rows.push(el("div", "phead2", "今の操作"));
    const question = ui.live && myAsk(v, seat);
    for (const r of (v.requests || {}).pending || []) {  // 審判が処理中の依頼（本人の分だけ届く）
      const what = { answer: "回答", mulligan: "マリガン" }[r.kind] || "依頼";
      rows.push(el("div", "phead2", `審判が処理中: ${r.player} の${what} #${r.seq}`),
        el("pre", "reqtext", r.text || what));
    }
    if (question) {
      // 審判の質問: 選択肢があればボタン、カードを選ぶ質問なら候補のカード、いつでも自由記述でも答えられる
      rows.push(el("div", "pask", `審判の質問 #${question.seq}: ${question.text}`));
      if ((question.cards || []).length) rows.push(cardAskBox(cardAsk()));
      row(...(question.choices || []).map((c) => button(c, () => answer(c))),
        button("自由に答える…", async () => {
          const r = await ask({ title: question.text, fields: [{ name: "text", label: "回答", value: "" }], ok: "答える" });
          if (r && r.text) answer(r.text);
        }));
    } else if (mine && t.turn === 0 && !hasKept(v, seat)) {
      const need = (v.pregame || {}).to_bottom || 0;
      row(button("キープ", () => declareKind("keep"),
        { title: need ? `この手札でキープする（下に置く ${need} 枚は審判が聞く）` : "この手札でキープする" }),
      button("マリガン", () => declareKind("mulligan"), { title: "手札をライブラリーに戻してシャッフルし、引き直す（審判が処理する）" }));
      if (mulligans(v, seat)) rows.push(el("div", "phint muted", `マリガン ${mulligans(v, seat)} 回。キープすると、審判が下に置く ${need} 枚を聞く`));
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
      rows.push(el("div", "phead2 sep", "発言・設定"));
      const strip = stopsStrip();
      if (strip) rows.push(strip);
      row(button("発言…", declare), !strip && button("止める場所…", editStops, { title: "審判がステップを進めるとき、止めてあなたに番を回す場所" }),
        button("投了", async () => { if (confirm("投了する？")) declareKind("concede"); }));
    }
    body.replaceChildren(status, ...rows);
  }

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
