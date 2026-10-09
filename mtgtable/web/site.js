// 公開のサーバーの画面の骨組み: ヘッダーの切り替え（トップ・デッキ・対局）と、トップの画面。
// 対局の画面は今までの盤面（app.js）。トップとデッキは、盤面の代わりに #page に描く。URL は #/top・#/decks・#/games
import { $, el } from "./dom.js";
import { createDecks } from "./decks.js";

export const PAGES = { top: "トップ", decks: "デッキ", games: "対局" };

export function pageOf(hash) {
  const m = /^#\/(\w+)/.exec(hash || "");
  return m && m[1] in PAGES ? m[1] : null;
}

export function createSite(source, { toast, games }) {
  const decks = createDecks(source, { toast });
  let current = null;

  function nav() {
    const box = $("sitenav");
    box.replaceChildren(...Object.entries(PAGES).map(([key, label]) => {
      const a = el("a", key === current ? "on" : null, label);
      a.href = `#/${key}`;
      return a;
    }));
    box.hidden = false;
  }

  function renderTop(root) {
    const mine = games();
    root.replaceChildren(
      el("h2", null, "mtgtable"),
      el("p", null, "紙の Magic: The Gathering を、ブラウザの卓で AI か招待した人と遊ぶ場所です。カードの処理は AI の審判が行います。"));
    const steps = el("ol", "steps");
    const item = (text, href, link) => {
      const li = el("li", null, text);
      if (href) {
        const a = el("a", null, link);
        a.href = href;
        li.append(" ", a);
      }
      steps.append(li);
    };
    item("デッキを登録する（デッキリストを貼り付けると、カード名と枚数を確かめます）。", "#/decks", "デッキへ");
    item(mine.length ? `対局する（あなたの対局: ${mine.length} 件）。` : "招待された URL を開くと、その席で対局できます。",
      mine.length ? "#/games" : null, "対局へ");
    item("別の端末でも続けるには、ヘッダーの「復元 URL」をその端末で開きます。");
    root.append(steps);
  }

  async function show(page) {
    current = page;
    nav();
    const board = page === "games";
    for (const sel of [".replay", "main.layout"]) document.querySelector(sel).hidden = !board;
    // 盤面にしか効かないヘッダーの部品（対局・席・画像・動き・接続・Log など）は、盤面のときだけ
    for (const id of ["game", "seat", "images", "motion"]) $(id).closest("label").hidden = !board;
    for (const id of ["conn", "logToggle", "turn"]) $(id).classList.toggle("offpage", !board);
    const root = $("page");
    root.hidden = board;
    document.body.classList.toggle("sitepage", !board);
    if (page === "top") renderTop(root);
    if (page === "decks") await decks.render(root);
  }

  return { show };
}
