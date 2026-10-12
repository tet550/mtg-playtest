// 履歴（手元のサイト）。終わった対局は盤面で再生する（#/view/<対局>。どの席の視点も、全体の視点も）。
import { el } from "./dom.js";

export const RESULT = { won: "勝ち", lost: "負け", ongoing: "対局中", over: "終了" };

export function viewURL(origin, game) {
  return `${origin}/#/view/${encodeURIComponent(game)}`;
}

// 「green（p1）対 piza（p2）」
export function matchup(players) {
  return players.map((p) => `${p.name}（${p.id}）`).join(" 対 ");
}

export function when(iso) {
  return iso ? iso.replace("T", " ").slice(0, 16) : "";
}

function button(text, onclick, cls) {
  const b = el("button", cls || null, text);
  b.type = "button";
  b.onclick = onclick;
  return b;
}

export function createHistory(source, { toast, openGame }) {
  async function renderHistory(root) {
    root.replaceChildren(el("h2", null, "履歴"));
    let games = [];
    try { games = await source.history(); } catch (e) { toast(e.message, true); }
    if (!games.length) {
      root.append(el("p", "muted", "まだ対局がありません。"));
      return;
    }
    const table = el("table", "decks");
    const head = el("tr");
    for (const h of ["日時", "対戦", "結果", "ターン", ""]) head.append(el("th", null, h));
    table.append(head);
    for (const g of games) {
      const tr = el("tr");
      const ops = el("td", "dops");
      const live = g.result === "ongoing";
      if (!live) ops.append(button("再生", () => { location.hash = `#/view/${g.id}`; }, "on"));
      else if (g.seat) ops.append(button("続ける", () => openGame(g.id), "on"));
      if (!live) {
        ops.append(button("消す", async () => {
          if (!window.confirm("この対局を消しますか？（対局の記録も消えます）")) return;
          try { await source.deleteGame(g.id); await renderHistory(root); } catch (e) { toast(e.message, true); }
        }));
      }
      const who = g.opponent === "human" ? "・対人戦" : "";
      tr.append(el("td", "muted", when(g.created)), el("td", "dname", matchup(g.players) + who),
        el("td", g.result === "won" ? "ok" : null, g.seat ? `${RESULT[g.result]}（${g.seat}）` : RESULT[g.result]),
        el("td", null, `T${g.turn}`), ops);
      table.append(tr);
    }
    root.append(table);
  }

  return { renderHistory };
}
