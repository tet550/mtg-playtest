// 履歴と公開の対局（公開のサーバー）。開くと盤面で再生する（#/view/<対局>[/<共有の鍵>]）。
import { el } from "./dom.js";

export const RESULT = { won: "勝ち", lost: "負け", ongoing: "対局中", over: "終了" };

export function viewURL(origin, game, share) {
  return `${origin}/#/view/${encodeURIComponent(game)}${share ? "/" + encodeURIComponent(share) : ""}`;
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

function urlBox(url) {
  const box = el("div", "urlbox");
  const input = el("input");
  input.readOnly = true;
  input.value = url;
  const copy = button("コピー", async () => {
    try { await navigator.clipboard.writeText(url); copy.textContent = "コピーした"; } catch (_) { input.select(); }
  });
  box.append(input, copy);
  return box;
}

export function createHistory(source, { toast, openGame }) {
  const fresh = {};  // 作った共有 URL（鍵はサーバーに残らないので、この画面にいる間だけ出せる）

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
      ops.append(button(live ? "続ける" : "再生", () => (live ? openGame(g.id) : (location.hash = `#/view/${g.id}`)), "on"),
        button("共有", () => toggleShare(table, tr, g)));
      if (!live) {
        ops.append(button("履歴から消す", async () => {
          const both = g.opponent === "human" ? "（相手も消すまで、相手の履歴には残ります）" : "（対局の記録も消えます）";
          if (!window.confirm(`この対局を履歴から消しますか？${both}`)) return;
          try { await source.hide(g.id); await renderHistory(root); } catch (e) { toast(e.message, true); }
        }));
      }
      const vis = g.visibility === "public" ? "公開" : "非公開";
      tr.append(el("td", "muted", when(g.created)), el("td", "dname", `${matchup(g.players)}・${vis}`),
        el("td", g.result === "won" ? "ok" : null, `${RESULT[g.result]}（${g.seat}）`), el("td", null, `T${g.turn}`), ops);
      table.append(tr);
    }
    root.append(table);
  }

  // 共有 URL の欄（その対局の行の下に開く）
  async function toggleShare(table, tr, g) {
    const next = tr.nextElementSibling;
    if (next && next.classList.contains("sharerow")) { next.remove(); return; }
    const row = el("tr", "sharerow");
    const td = el("td");
    td.colSpan = 5;
    row.append(td);
    tr.after(row);
    const draw = async () => {
      td.replaceChildren(el("div", "muted", "見る人が盤面を再生できる URL（書き込みはできない）。いつでも取り消せます"));
      const live = g.result === "ongoing";
      const buttons = el("div", "crow");
      const make = (view, label) => button(label, async () => {
        try {
          const s = await source.createShare(g.id, view);
          fresh[s.id] = viewURL(location.origin, g.id, s.token);
          await draw();
        } catch (e) { toast(e.message, true); }
      });
      buttons.append(make(g.seat, `自分の席（${g.seat}）の視点`));
      if (!live) buttons.append(make("judge", "全体の視点（両方の手札も見える）"));
      if (!live && g.opponent === "human") {
        buttons.append(button("相手が全体の視点を共有するのに同意する", async () => {
          try { await source.consent(g.id); toast("同意した"); } catch (e) { toast(e.message, true); }
        }));
      }
      td.append(buttons);
      let shares = [];
      try { shares = await source.shares(g.id); } catch (_) { shares = []; }
      for (const s of shares) {
        const line = el("div", "crow");
        line.append(el("span", null, `${s.view === "judge" ? "全体" : s.view} の視点・${when(s.created)}`),
          button("取り消す", async () => {
            try { await source.revokeShare(g.id, s.id); delete fresh[s.id]; await draw(); } catch (e) { toast(e.message, true); }
          }));
        td.append(line);
        if (fresh[s.id]) td.append(urlBox(fresh[s.id]));
      }
    };
    await draw();
  }

  async function renderPublic(root) {
    root.replaceChildren(el("h2", null, "公開の対局"), el("p", "muted", "終わった公開の対局を、誰でも再生できます。"));
    const filter = el("form", "crow");
    const deck = el("input");
    deck.placeholder = "デッキ名で絞り込む";
    const go = el("button", null, "絞り込む");
    go.type = "submit";
    filter.append(deck, go);
    const list = el("div");
    root.append(filter, list);
    let before = null;
    const more = button("もっと見る", () => load(false));
    const table = el("table", "decks");
    async function load(reset) {
      if (reset) { before = null; table.replaceChildren(); }
      let games = [];
      try { games = await source.publicGames(deck.value.trim() || null, before); } catch (e) { toast(e.message, true); }
      for (const g of games) {
        const tr = el("tr");
        const winner = g.players.find((p) => p.id === g.winner);
        tr.append(el("td", "muted", when(g.ended)), el("td", "dname", matchup(g.players)),
          el("td", null, winner ? `${winner.name} の勝ち` : "—"), el("td", null, `T${g.turn}`));
        const ops = el("td", "dops");
        ops.append(button("再生", () => { location.hash = `#/view/${g.id}`; }, "on"));
        tr.append(ops);
        table.append(tr);
      }
      if (games.length) before = games[games.length - 1].ended;
      list.replaceChildren(table.childElementCount ? table : el("p", "muted", "まだありません。"));
      if (games.length >= 50) list.append(more);
    }
    filter.onsubmit = (e) => { e.preventDefault(); load(true); };
    await load(true);
  }

  return { renderHistory, renderPublic };
}
