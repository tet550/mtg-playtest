// スタックの窓。窓は下端で留め、上に積む（後から積んだものが一番上。積む・解決で変わるのは一番上の項目だけで、
// 下の項目と窓の下端は動かない）。項目の出入りは一覧の中で見せる（写しを重ねない）ので、窓・行・カードが重ならず、
// 一覧の外へはみ出さない。
// 出入りする行は、上の余白を負にして一覧の上の縁の外へずらす: 行の下端（その下の行の上）と中身はその場から動かず、
// 一覧の上の縁（窓の上端）が中身の上を通って開く・閉じる。呪文のカードの出入りはカードの動き（motion.js）が見せる。
import { patch } from "./patch.js";

// 解決した項目が光っている間（解決の光が対象に当たるまで）。呪文のカードはその間スタックに留まり、それから行き先へ飛ぶ
export const GLOW = 420;
const EASE = "cubic-bezier(.2, .7, .2, 1)";
const SHOW = 250;  // 空だった窓が出てくる（戦闘の窓が左へ退く間。CSS の .combatbox の transition と同じ長さ）
const PUSHED = 520;  // 積まれた行の縁が光る

// box: 窓（#fl-stack）、list: 一覧（#stack）
export function createStack(box, list) {
  let running = [];
  let afterAll = null;  // 動きが全部終わったときの片付け（最後の項目が抜けた窓を隠す）

  // 動いている途中なら、すぐ終わらせる（次の描画の前）
  function stop() {
    for (const a of running) a.cancel();
    running = [];
    for (const li of [...list.children]) if (li.classList.contains("leaving")) li.remove();
    if (afterAll) afterAll();
    afterAll = null;
  }

  function run(target, frames, timing, done) {
    const a = target.animate(frames, { fill: "both", ...timing });
    running.push(a);
    a.onfinish = () => {
      running = running.filter((x) => x !== a);
      a.cancel();
      if (done) done();
      if (!running.length && afterAll) { afterAll(); afterAll = null; }
    };
  }

  // 行が一覧の中で取る高さ（下の余白を含む）。この分だけ上へずらすと、行は一覧の上の縁の外に出る
  const space = (li) => li.offsetHeight + parseFloat(getComputedStyle(li).marginBottom);

  // rows: 新しく組み立てた行（li[data-key]、一番上から）。motion: 動かすときだけ { duration, forward }
  function update(rows, motion) {
    stop();
    const keys = new Set(rows.map((li) => li.dataset.key));
    const shown = !box.hidden;
    const anim = motion && !box.classList.contains("min") ? motion : null;
    const was = anim && shown ? [...list.children] : [];
    // 抜ける行は、描き直しの前の位置に写しを残す（次に残る行の前に差し込む）
    const gone = was.map((li, i) => ({ li, next: was.slice(i + 1).find((x) => keys.has(x.dataset.key)) }))
      .filter(({ li }) => !keys.has(li.dataset.key))
      .map(({ li, next }) => ({ copy: leavingCopy(li), next: next ? next.dataset.key : null, space: space(li), pv: li.classList.contains("pv") }));
    const known = new Set(was.map((li) => li.dataset.key));
    patch(list, rows);
    box.hidden = !rows.length && !gone.length;
    if (!anim || box.hidden) return;
    if (!shown) run(box, [{ opacity: 0 }, { opacity: 1 }], { duration: SHOW, easing: "ease-in" });  // 退きかけの戦闘の窓と重なって見えない

    for (const g of gone) {
      const next = g.next && [...list.children].find((x) => x.dataset.key === g.next);
      list.insertBefore(g.copy, next || null);
      const off = { marginTop: `${-g.space}px`, opacity: 0 };
      if (anim.forward && !g.pv) {
        // 解決: 光って、光の玉が対象に当たったら閉じる（呪文のカードは同じときに行き先へ飛び出す）
        g.copy.classList.add("resolving");
        const total = GLOW + anim.duration;
        const at = GLOW / total;
        run(g.copy, [
          { marginTop: "0px", opacity: 1, filter: "brightness(1)", easing: "ease-out" },
          { filter: "brightness(1.3)", offset: at * 0.5, easing: "ease-in-out" },
          { marginTop: "0px", opacity: 1, filter: "brightness(1.1)", offset: at, easing: EASE },
          { ...off, filter: "brightness(1.1)" }],
        { duration: total }, () => g.copy.remove());
      } else {
        // 戻す・下書きの仮の項目が消える: そのまま閉じる
        run(g.copy, [{ marginTop: "0px", opacity: 1 }, off], { duration: anim.duration * 0.7, easing: EASE }, () => g.copy.remove());
      }
    }
    // 積まれた行は開いていく（呪文のカードは手札などから飛んできて、この行の置き場所に収まる）。開いたら縁が光る
    for (const li of [...list.children]) {
      if (known.has(li.dataset.key) || li.classList.contains("leaving")) continue;
      run(li, [{ marginTop: `${-space(li)}px`, opacity: 0 }, { marginTop: "0px", opacity: 1, offset: 0.8 }, { marginTop: "0px", opacity: 1 }],
        { duration: anim.duration, easing: EASE }, () => run(li, [
          { boxShadow: "inset 0 0 0 2px rgba(255, 216, 74, 0)" },
          { boxShadow: "inset 0 0 0 2px rgba(255, 216, 74, .95)", offset: 0.3 },
          { boxShadow: "inset 0 0 0 2px rgba(255, 216, 74, 0)" }], { duration: PUSHED, easing: "ease-out" }));
    }
    // 最後の項目が抜けたら、閉じ終えてから窓を隠す
    if (!rows.length) afterAll = () => { if (!list.querySelector(":scope > li:not(.leaving)")) box.hidden = true; };
  }

  // 抜ける行の写し: 押せない・カードとして数えない（data-ids を外す）。呪文のカードはカードの動きが運ぶので隠す
  function leavingCopy(li) {
    const g = li.cloneNode(true);
    g.classList.add("leaving");
    g.removeAttribute("data-key");
    g.removeAttribute("data-target");
    const spell = !li.classList.contains("ability");
    for (const c of g.querySelectorAll("[data-ids]")) {
      c.removeAttribute("data-ids");
      if (spell) c.style.visibility = "hidden";
    }
    return g;
  }

  return { update, stop };
}
