// 手元のサイトの画面の骨組み: ヘッダーの切り替え（トップ・デッキ・対局）と、トップの画面。
// 対局の画面は今までの盤面（app.js）。トップとデッキは、盤面の代わりに #page に描く。URL は #/top・#/decks・#/games
import { $, el } from "./dom.js";
import { createDecks } from "./decks.js";
import { createLobby } from "./lobby.js";
import { createHistory } from "./history.js";
import { t } from "./i18n.js";

// ヘッダーのメニューの画面（名前は辞書の nav.<画面>）
export const PAGES = ["top", "decks", "new", "games", "history"];
// ヘッダーに出さない画面（招待の URL: #/join/<招待>/<鍵>、終わった対局の再生: #/view/<対局>）
const HIDDEN = ["join", "view"];
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

  let tutorialBusy = false;  // チュートリアルを作っている間（初回はカードの情報を引くので時間がかかる）
  const host = () => !!config().host;  // ゲスト（招待で着いた外のブラウザ）にはメニューを出さない

  function nav() {
    const box = $("sitenav");
    if (!host()) {
      box.hidden = true;
      return;
    }
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
    const tutorial = link(tutorialBusy ? "busy" : null, t(tutorialBusy ? "top.cta.tutorial.busy" : "top.cta.tutorial"), "#/games");
    tutorial.setAttribute("aria-busy", String(tutorialBusy));
    tutorial.onclick = async (ev) => {  // 決まった台本で1局（AI は使わない）
      ev.preventDefault();
      if (tutorialBusy) return;  // 準備の途中で押しても、重ねて作らない
      tutorialBusy = true;
      if (current === "top") renderTop(root);
      toast(t("top.cta.tutorial.wait"));
      try {
        const r = await source.startTutorial();
        try { localStorage.removeItem(`draft.${r.game}.${r.seat}`); } catch (_) { /* 保存できない */ }  // 前の回の下書きを持ち越さない
        await openGame(r.game);
      } catch (e) { toast(e.message, true); } finally {
        tutorialBusy = false;
        if (current === "top") renderTop($("page"));
      }
    };
    cta.append(tutorial, mine.length ? link("primary", t("top.cta.games"), "#/games") : link("primary", t("top.cta.decks"), "#/decks"),
      link(null, t("top.cta.new"), "#/new"), link(null, t("top.cta.history"), "#/history"));
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
    item(t("top.step.history"), "#/history", t("top.step.history.link"));
    root.append(steps);
    // 審判と AI の席は Claude Code が回す
    root.append(el("p", "muted", t("top.ai.claudeCode")));
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
    document.body.classList.toggle("sitepage", !board);
    if (page === "top") renderTop(root);
    if (page === "decks") await decks.render(root);
    if (page === "new") await lobby.render(root);
    if (page === "join") await lobby.renderJoin(root, args[0], args[1]);
    if (page === "history") await history.renderHistory(root);
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
