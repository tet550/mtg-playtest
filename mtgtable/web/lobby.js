// 対局を作る画面（手元のサイト）: 登録したデッキか同梱のデッキで、AI と・招待した人と。招待の URL を開いた人（ゲスト）の「席に着く」画面も。
import { el } from "./dom.js";
import { FORMATS, checkLines } from "./decks.js";

export function inviteURL(origin, invite, token) {
  return `${origin}/#/join/${encodeURIComponent(invite)}/${encodeURIComponent(token)}`;
}

// 招待の状態の表示
export function inviteState(i) {
  if (i.game) return "対局になった";
  if (i.expired) return "期限切れ";
  return "相手待ち";
}

function select(options, value) {
  const s = el("select");
  for (const [k, label] of options) {
    const o = el("option", null, label);
    o.value = k;
    s.append(o);
  }
  if (value !== undefined) s.value = value;
  return s;
}

function field(label, input, help) {
  const box = el("label", "field");
  box.append(el("span", "flabel", label), input);
  if (help) box.append(el("span", "fhelp muted", help));
  return box;
}

function urlBox(url) {
  const box = el("div", "urlbox");
  const input = el("input");
  input.readOnly = true;
  input.value = url;
  const copy = el("button", null, "コピー");
  copy.type = "button";
  copy.onclick = async () => {
    try { await navigator.clipboard.writeText(url); copy.textContent = "コピーした"; } catch (_) { input.select(); }
  };
  box.append(input, copy);
  return box;
}

export function createLobby(source, { toast, openGame, active }) {
  let timer = null;
  const fresh = {};  // 作った・作り直した招待の URL（鍵はサーバーに残らないので、この画面にいる間だけ出せる）

  async function render(root) {
    clearInterval(timer);
    root.replaceChildren(el("h2", null, "対局を作る"));
    let decks = [], repo = [];
    try { decks = await source.decks(); } catch (e) { toast(e.message, true); }
    try { repo = await source.aiDecks(); } catch (_) { repo = []; }
    if (!decks.length) {
      const p = el("p", "muted", "デッキを登録していないので、用意したデッキで対局します。自分のデッキを使うときは");
      const a = el("a", null, "デッキへ");
      a.href = "#/decks";
      p.append(" ", a);
      root.append(p);
    }
    if (decks.length || repo.length) root.append(form(decks, repo));
    const list = el("div", "invites");
    root.append(el("h3", null, "招待"), list);
    await renderInvites(list);
    // 相手が着いたら分かるように、相手待ちの招待がある間は見に行く
    timer = setInterval(() => {
      if (!active()) return clearInterval(timer);
      renderInvites(list).catch(() => {});
    }, 4000);
  }

  function form(decks, repo) {
    const f = el("form", "deckform");
    const mine = select([...decks.map((d) => [`deck:${d.id}`, `${d.name}（${d.main} 枚）`]),
      ...repo.map((n) => [`repo:${n}`, `用意したデッキ: ${n}`])]);
    const opponent = select([["ai", "AI と対戦する"], ["human", "人を招待して対戦する"]]);
    const aiDeck = select([...repo.map((n) => [`repo:${n}`, `用意したデッキ: ${n}`]),
      ...decks.map((d) => [`deck:${d.id}`, `登録したデッキ: ${d.name}`])]);
    const aiField = field("AI のデッキ", aiDeck, "審判と AI の席は Claude Code が回します（mtg-playtest スキルの GUI の対局）");
    opponent.onchange = () => { aiField.hidden = opponent.value !== "ai"; };
    const go = el("button", "on", "対局を作る");
    go.type = "submit";
    const out = el("div", "checkresult");
    f.onsubmit = async (e) => {
      e.preventDefault();
      go.disabled = true;
      out.replaceChildren(el("div", "muted", "用意しています…"));
      try {
        const r = await source.createGame({ deck: mine.value, opponent: opponent.value, ai_deck: aiDeck.value });
        if (r.game) return openGame(r.game);
        fresh[r.invite] = inviteURL(location.origin, r.invite, r.token);
        out.replaceChildren(el("div", "ok", "招待を作りました。下の一覧の URL を相手にだけ渡します（1回だけ使える・3日で切れる）"));
        await renderInvites(document.querySelector(".invites"));
      } catch (err) {
        out.replaceChildren(el("div", "bad", err.message));
      } finally {
        go.disabled = false;
      }
    };
    const buttons = el("div", "crow");
    buttons.append(go);
    f.append(field("自分のデッキ", mine), field("相手", opponent, "招待した人は、この PC のサーバーに届くブラウザから参加します（serve --host）"),
      aiField, buttons, out);
    return f;
  }

  async function renderInvites(list) {
    if (!list) return;
    let invites = [];
    try { invites = await source.invites(); } catch (_) { return; }
    if (!invites.length) {
      list.replaceChildren(el("p", "muted", "まだ招待はありません。"));
      return;
    }
    const table = el("table", "decks");
    for (const i of invites) {
      const tr = el("tr");
      const ops = el("td", "dops");
      if (i.game) {
        const open = el("button", "on", "開く");
        open.type = "button";
        open.onclick = () => openGame(i.game);
        ops.append(open);
      } else {
        const renew = el("button", null, "URL を作り直す");
        renew.type = "button";
        renew.onclick = async () => {
          try {
            const r = await source.renewInvite(i.id);
            fresh[i.id] = inviteURL(location.origin, i.id, r.token);
            await renderInvites(list);
          } catch (e) { toast(e.message, true); }
        };
        const cancel = el("button", null, "取り消す");
        cancel.type = "button";
        cancel.onclick = async () => {
          try { await source.cancelInvite(i.id); delete fresh[i.id]; await renderInvites(list); } catch (e) { toast(e.message, true); }
        };
        ops.append(renew, cancel);
      }
      tr.append(el("td", "dname", i.deck), el("td", null, inviteState(i)),
        el("td", "muted", i.created.replace("T", " ")), ops);
      table.append(tr);
      if (fresh[i.id] && !i.game) {
        const r2 = el("tr");
        const td = el("td");
        td.colSpan = 4;
        td.append(urlBox(fresh[i.id]));
        r2.append(td);
        table.append(r2);
      }
    }
    list.replaceChildren(table);
  }

  // 招待の URL を開いたゲスト: デッキリストを貼るか、同梱のデッキを選んで席に着く（登録はしない）
  async function renderJoin(root, invite, token) {
    clearInterval(timer);
    root.replaceChildren(el("h2", null, "招待された対局"));
    let info;
    try {
      info = await source.invite(invite, token);
    } catch (e) {
      root.append(el("p", "bad", e.status === 403 ? `この招待は使えません（${e.message}）` : e.message));
      return;
    }
    root.append(el("p", null, `相手のデッキ: ${info.deck}（${info.format}）`),
      el("p", "muted", `招待の期限: ${info.expires.replace("T", " ")}`));
    const f = el("form", "deckform");
    const kind = select([["text", "デッキリストを貼る"], ...(info.repo_decks.length ? [["repo", "用意したデッキを使う"]] : [])]);
    const name = el("input");
    name.placeholder = "guest";
    name.maxLength = 40;
    const format = select(FORMATS, info.format);
    const text = el("textarea");
    text.rows = 14;
    text.placeholder = "4 Lightning Bolt\n20 Mountain\n…";
    const repo = select(info.repo_decks.map((n) => [n, n]));
    const textFields = [field("デッキ名", name), field("フォーマット", format), field("デッキリスト", text)];
    const repoField = field("用意したデッキ", repo);
    const sync = () => {
      for (const x of textFields) x.hidden = kind.value !== "text";
      repoField.hidden = kind.value !== "repo";
    };
    kind.onchange = sync;
    const go = el("button", "on", "このデッキで席に着く");
    go.type = "submit";
    const out = el("div", "checkresult");
    f.onsubmit = async (e) => {
      e.preventDefault();
      go.disabled = true;
      out.replaceChildren(el("div", "muted", "用意しています…"));
      try {
        const deck = kind.value === "repo" ? { repo: repo.value }
          : { text: text.value, format: format.value, name: name.value.trim() || "guest" };
        const r = await source.joinInvite(invite, token, deck);
        openGame(r.game, { seat: r.seat, token: r.token });
      } catch (err) {
        out.replaceChildren(el("div", "bad", err.message),
          ...checkLines(err.check).map((x) => el("div", x.kind === "error" ? "bad" : "muted", x.text)));
        go.disabled = false;
      }
    };
    const buttons = el("div", "crow");
    buttons.append(go);
    f.append(field("デッキ", kind), ...textFields, repoField, buttons, out);
    sync();
    root.append(f);
  }

  return { render, renderJoin, stop: () => clearInterval(timer) };
}
