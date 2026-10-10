// 公開のサーバーの画面の骨組み: ヘッダーの切り替え（トップ・デッキ・対局）と、トップの画面。
// 対局の画面は今までの盤面（app.js）。トップとデッキは、盤面の代わりに #page に描く。URL は #/top・#/decks・#/games
import { $, el } from "./dom.js";
import { createDecks } from "./decks.js";
import { createLobby } from "./lobby.js";
import { createHistory } from "./history.js";
import { renderPrivacy, renderTerms } from "./legal.js";
import { t } from "./i18n.js";

// ヘッダーのメニューの画面（名前は辞書の nav.<画面>）
export const PAGES = ["top", "decks", "new", "games", "history", "public"];
// ヘッダーに出さない画面（招待の URL: #/join/<招待>/<鍵>、再生・観戦: #/view/<対局>[/<共有の鍵>]）
const HIDDEN = ["join", "view", "terms", "privacy"];
export const BOARD_PAGES = ["games", "view"];  // 盤面を出す画面

// #/decks → {page: "decks", args: []}、#/join/abc/xyz → {page: "join", args: ["abc", "xyz"]}
export function routeOf(hash) {
  const m = /^#\/(\w+)((?:\/[^/]*)*)$/.exec(hash || "");
  if (!m || !(PAGES.includes(m[1]) || HIDDEN.includes(m[1]))) return null;
  return { page: m[1], args: m[2].split("/").slice(1).map(decodeURIComponent) };
}

export function pageOf(hash) {
  const r = routeOf(hash);
  return r ? r.page : null;
}

export function createSite(source, { toast, games, openGame, config = () => ({}) }) {
  const decks = createDecks(source, { toast });
  let current = null;
  const lobby = createLobby(source, { toast, openGame, active: () => current === "new" });
  const history = createHistory(source, { toast, openGame });

  function nav() {
    const box = $("sitenav");
    box.replaceChildren(...PAGES.map((key) => {
      const a = el("a", key === current ? "on" : null, t(`nav.${key}`));
      a.href = `#/${key}`;
      return a;
    }));
    box.hidden = false;
  }

  function renderTop(root) {
    const mine = games();
    const link = (cls, text, href) => {
      const a = el("a", cls, text);
      a.href = href;
      return a;
    };
    const cta = el("div", "cta");
    cta.append(mine.length ? link("primary", t("top.cta.games"), "#/games") : link("primary", t("top.cta.decks"), "#/decks"),
      link(null, t("top.cta.new"), "#/new"), link(null, t("top.cta.public"), "#/public"));
    const hero = el("div", "hero");
    hero.append(
      el("h2", null, t("top.title")),
      el("p", null, t("top.lead")),
      cta);
    root.replaceChildren(hero);
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
    item(t("top.step.deck"), "#/decks", t("top.step.deck.link"));
    item(t("top.step.new"), "#/new", t("top.step.new.link"));
    item(mine.length ? t("top.step.play.mine", { count: mine.length }) : t("top.step.play.invited"),
      mine.length ? "#/games" : null, t("top.step.play.link"));
    item(t("top.step.history"), "#/public", t("top.step.history.link"));
    item(t("top.step.recovery"));
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
    for (const id of ["conn", "logToggle", "turn", "thinking", "playing", "fl-play"]) $(id).classList.toggle("offpage", !board);
    const root = $("page");
    root.hidden = board;
    root.classList.remove("legal");
    document.body.classList.toggle("sitepage", !board);
    if (page === "top") renderTop(root);
    if (page === "decks") await decks.render(root);
    if (page === "new") await lobby.render(root);
    if (page === "join") await lobby.renderJoin(root, args[0], args[1]);
    if (page === "history") await history.renderHistory(root);
    if (page === "public") await history.renderPublic(root);
    if (page === "terms") renderTerms(root, config());
    if (page === "privacy") renderPrivacy(root, config());
    if (!board) window.scrollTo(0, 0);
  }

  // 言語を変えたとき: メニューと、訳してある画面（今はトップだけ）を描き直す。入力の途中の画面（デッキなど）は描き直さない
  function relabel() {
    if (!current) return;
    nav();
    if (current === "top") renderTop($("page"));
  }

  return { show, relabel };
}
