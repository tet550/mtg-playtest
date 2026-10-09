// 対局を作る画面（公開のサーバー）: 自分のデッキで、AI と・招待した人と。招待の URL を開いた人の「席に着く」画面も。
import { el } from "./dom.js";

export const VISIBILITY = [["public", "公開（終わった後、誰でも見られる）"], ["private", "非公開（参加者だけ）"]];

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
    let decks = [];
    try { decks = await source.decks(); } catch (e) { toast(e.message, true); }
    if (!decks.length) {
      const p = el("p", null, "先にデッキを登録します。");
      const a = el("a", null, "デッキへ");
      a.href = "#/decks";
      p.append(" ", a);
      root.append(p);
    } else {
      root.append(await form(decks));
    }
    const list = el("div", "invites");
    root.append(el("h3", null, "招待"), list);
    await renderInvites(list);
    // 相手が着いたら分かるように、相手待ちの招待がある間は見に行く
    timer = setInterval(() => {
      if (!active()) return clearInterval(timer);
      renderInvites(list).catch(() => {});
    }, 4000);
  }

  async function form(decks) {
    const f = el("form", "deckform");
    const mine = select(decks.map((d) => [d.id, `${d.name}（${d.main} 枚）`]));
    const opponent = select([["ai", "AI と対戦する"], ["human", "人を招待して対戦する"]]);
    let aiDecks = [];
    try { aiDecks = await source.aiDecks(); } catch (_) { aiDecks = []; }
    const aiDeck = select([...aiDecks.map((n) => [`repo:${n}`, `用意したデッキ: ${n}`]),
      ...decks.map((d) => [`deck:${d.id}`, `自分のデッキ: ${d.name}`])]);
    const aiField = field("AI のデッキ", aiDeck);
    const visibility = select(VISIBILITY, "public");
    opponent.onchange = () => { aiField.hidden = opponent.value !== "ai"; };
    const go = el("button", "on", "対局を作る");
    go.type = "submit";
    const out = el("div", "checkresult");
    f.onsubmit = async (e) => {
      e.preventDefault();
      go.disabled = true;
      out.replaceChildren(el("div", "muted", "用意しています…"));
      try {
        const r = await source.createGame({ deck: mine.value, opponent: opponent.value, ai_deck: aiDeck.value,
          visibility: visibility.value });
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
    f.append(field("自分のデッキ", mine), field("相手", opponent), aiField,
      field("公開", visibility, "公開の対局は、終わった後に一覧に出て誰でも再生できる"), buttons, out);
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
    const vis = (VISIBILITY.find(([k]) => k === info.visibility) || [])[1] || info.visibility;
    root.append(el("p", null, `相手のデッキ: ${info.deck}（${info.format}）`), el("p", "muted", `${vis}。招待の期限: ${info.expires.replace("T", " ")}`));
    let decks = [];
    try { decks = await source.decks(); } catch (_) { decks = []; }
    if (!decks.length) {
      const p = el("p", null, "席に着くには、先にデッキを登録します（登録したら、もう一度この URL を開く）。");
      const a = el("a", null, "デッキへ");
      a.href = "#/decks";
      p.append(" ", a);
      root.append(p);
      return;
    }
    const f = el("form", "deckform");
    const mine = select(decks.map((d) => [d.id, `${d.name}（${d.main} 枚）`]));
    const go = el("button", "on", "このデッキで席に着く");
    go.type = "submit";
    const out = el("div", "checkresult");
    f.onsubmit = async (e) => {
      e.preventDefault();
      go.disabled = true;
      try {
        const r = await source.joinInvite(invite, token, mine.value);
        openGame(r.game);
      } catch (err) {
        out.replaceChildren(el("div", "bad", err.message));
        go.disabled = false;
      }
    };
    const buttons = el("div", "crow");
    buttons.append(go);
    f.append(field("自分のデッキ", mine, `席に着くと、この対局は${vis.split("（")[0]}になることに同意したことになります`), buttons, out);
    root.append(f);
  }

  return { render, renderJoin, stop: () => clearInterval(timer) };
}
