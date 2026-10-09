// 公開のサーバーの画面の骨組み: ヘッダーの切り替え（トップ・デッキ・対局）と、トップの画面。
// 対局の画面は今までの盤面（app.js）。トップとデッキは、盤面の代わりに #page に描く。URL は #/top・#/decks・#/games
import { $, el } from "./dom.js";
import { createDecks } from "./decks.js";
import { createLobby } from "./lobby.js";
import { createHistory } from "./history.js";

export const PAGES = { top: "トップ", decks: "デッキ", new: "対局を作る", games: "対局", history: "履歴", public: "公開の対局" };
// ヘッダーに出さない画面（招待の URL: #/join/<招待>/<鍵>、再生・観戦: #/view/<対局>[/<共有の鍵>]）
const HIDDEN = ["join", "view"];
export const BOARD_PAGES = ["games", "view"];  // 盤面を出す画面

// #/decks → {page: "decks", args: []}、#/join/abc/xyz → {page: "join", args: ["abc", "xyz"]}
export function routeOf(hash) {
  const m = /^#\/(\w+)((?:\/[^/]*)*)$/.exec(hash || "");
  if (!m || !(m[1] in PAGES || HIDDEN.includes(m[1]))) return null;
  return { page: m[1], args: m[2].split("/").slice(1).map(decodeURIComponent) };
}

export function pageOf(hash) {
  const r = routeOf(hash);
  return r ? r.page : null;
}

export function createSite(source, { toast, games, openGame }) {
  const decks = createDecks(source, { toast });
  let current = null;
  const lobby = createLobby(source, { toast, openGame, active: () => current === "new" });
  const history = createHistory(source, { toast, openGame });

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
    item("対局を作る（AI と対戦するか、人を招待する）。", "#/new", "対局を作るへ");
    item(mine.length ? `対局する（あなたの対局: ${mine.length} 件）。` : "招待された URL を開くと、その席で対局できます。",
      mine.length ? "#/games" : null, "対局へ");
    item("終わった対局は「履歴」から再生でき、共有 URL で人に見せられます。公開の対局は「公開の対局」で誰でも再生できます。",
      "#/public", "公開の対局へ");
    item("別の端末でも続けるには、ヘッダーの「復元 URL」をその端末で開きます。");
    root.append(steps);
  }

  async function show(page, args = []) {
    current = page;
    if (page !== "new") lobby.stop();
    nav();
    const board = BOARD_PAGES.includes(page);
    for (const sel of [".replay", "main.layout"]) document.querySelector(sel).hidden = !board;
    // 盤面にしか効かないヘッダーの部品（対局・席・画像・動き・接続・Log など）は、盤面のときだけ
    for (const id of ["game", "seat", "images", "motion"]) $(id).closest("label").hidden = !board;
    for (const id of ["conn", "logToggle", "turn", "playing", "fl-play"]) $(id).classList.toggle("offpage", !board);
    const root = $("page");
    root.hidden = board;
    document.body.classList.toggle("sitepage", !board);
    if (page === "top") renderTop(root);
    if (page === "decks") await decks.render(root);
    if (page === "new") await lobby.render(root);
    if (page === "join") await lobby.renderJoin(root, args[0], args[1]);
    if (page === "history") await history.renderHistory(root);
    if (page === "public") await history.renderPublic(root);
  }

  return { show };
}
