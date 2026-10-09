// デッキの画面（公開のサーバー）: 自分のデッキの一覧と、登録・編集（貼り付け → 検査 → 保存）・削除。
import { el } from "./dom.js";

export const FORMATS = [
  ["standard", "スタンダード"], ["pioneer", "パイオニア"], ["modern", "モダン"], ["legacy", "レガシー"],
  ["vintage", "ヴィンテージ"], ["pauper", "パウパー"], ["historic", "ヒストリック"], ["timeless", "タイムレス"],
  ["free", "自由（フォーマットを見ない）"],
];

// 検査の結果を、画面に出す行にする: 行番号のある理由を先に、行の順に
export function checkLines(result) {
  if (!result) return [];
  const row = (kind) => (e) => ({ kind, text: (e.line ? `${e.line} 行目: ` : "") + e.message, line: e.line || null });
  return [...(result.errors || []).map(row("error")), ...(result.warnings || []).map(row("warning"))];
}

export function summary(result) {
  if (!result) return "";
  const size = `メイン ${result.main} 枚・サイドボード ${result.sideboard} 枚`;
  if (!result.ok) return `${size}。直す所が ${result.errors.length} 件あります`;
  return result.saved ? `${size}。保存しました` : `${size}。登録できます`;
}

export function createDecks(source, { toast }) {
  let editing = null;  // 編集中のデッキの id（新規なら null）

  function field(label, input, help) {
    const box = el("label", "field");
    box.append(el("span", "flabel", label), input);
    if (help) box.append(el("span", "fhelp muted", help));
    return box;
  }

  async function render(root) {
    root.replaceChildren(el("h2", null, "デッキ"));
    const list = el("div", "decklist");
    const form = el("form", "deckform");
    root.append(list, form);
    await renderList(list, form);
    renderForm(form, null, list);
  }

  async function renderList(list, form) {
    list.replaceChildren();
    let decks = [];
    try { decks = await source.decks(); } catch (e) { toast(e.message, true); }
    if (!decks.length) {
      list.append(el("p", "muted", "まだデッキがありません。下にデッキリストを貼り付けて登録します。"));
      return;
    }
    const table = el("table", "decks");
    const head = el("tr");
    for (const h of ["名前", "フォーマット", "枚数", "更新", ""]) head.append(el("th", null, h));
    table.append(head);
    for (const d of decks) {
      const tr = el("tr");
      const edit = el("button", null, "編集");
      edit.type = "button";
      edit.onclick = async () => renderForm(form, await source.deck(d.id), list);
      const del = el("button", null, "削除");
      del.type = "button";
      del.onclick = async () => {
        if (!window.confirm(`デッキ「${d.name}」を消しますか？（対局の記録は残ります）`)) return;
        try {
          await source.deleteDeck(d.id);
          if (editing === d.id) renderForm(form, null, list);
          await renderList(list, form);
        } catch (e) { toast(e.message, true); }
      };
      const fmt = (FORMATS.find(([k]) => k === d.format) || [d.format, d.format])[1];
      tr.append(el("td", "dname", d.name), el("td", null, fmt), el("td", null, `${d.main} / ${d.sideboard}`),
        el("td", "muted", d.updated.replace("T", " ")));
      const ops = el("td", "dops");
      ops.append(edit, del);
      tr.append(ops);
      table.append(tr);
    }
    list.append(table);
  }

  function renderForm(form, deck, list, initial = null) {
    editing = deck ? deck.id : null;
    form.replaceChildren(el("h3", null, deck ? `「${deck.name}」を編集` : "新しいデッキ"));
    const name = el("input");
    name.value = deck ? deck.name : "";
    name.placeholder = "mono-green";
    name.maxLength = 40;
    const format = el("select");
    for (const [k, label] of FORMATS) {
      const o = el("option", null, label);
      o.value = k;
      format.append(o);
    }
    format.value = deck ? deck.format : "standard";
    const text = el("textarea", "decktext");
    text.rows = 16;
    text.value = deck ? deck.text : "";
    text.placeholder = "Deck\n4 Llanowar Elves\n...\n20 Forest\n\nSideboard\n2 Naturalize";
    const strategy = el("textarea", "strategy");
    strategy.rows = 4;
    strategy.value = deck ? deck.strategy : "";
    strategy.placeholder = "AI がこのデッキを使うときの方針（任意）: キープの基準・勝ち筋・注意するカードなど";
    const result = el("div", "checkresult");
    const show = (r) => {
      result.replaceChildren(el("div", r && r.ok ? "ok" : "bad", summary(r)));
      const ul = el("ul");
      for (const l of checkLines(r)) ul.append(el("li", l.kind, l.text));
      result.append(ul);
    };
    const body = () => ({ name: name.value.trim(), format: format.value, text: text.value, strategy: strategy.value });
    const check = el("button", null, "検査する");
    check.type = "button";
    check.onclick = async () => {
      check.disabled = true;
      result.replaceChildren(el("div", "muted", "カード名を確かめています…"));
      try { show(await source.checkDeck(text.value, format.value)); } catch (e) { toast(e.message, true); }
      check.disabled = false;
    };
    const save = el("button", "on", deck ? "保存する" : "登録する");
    save.type = "submit";
    form.onsubmit = async (e) => {
      e.preventDefault();
      save.disabled = true;
      result.replaceChildren(el("div", "muted", "検査しています…"));
      try {
        const saved = await source.saveDeck(editing, body());
        toast(deck ? "保存した" : "登録した");
        renderForm(form, saved, list,  // 描き直した画面に結果を出す（注意があればそれも）
          { ok: true, saved: true, errors: [], warnings: saved.warnings || [], main: saved.main, sideboard: saved.sideboard });
        await renderList(list, form);
      } catch (err) {
        if (err.check) show(err.check); else toast(err.message, true);
      }
      save.disabled = false;
    };
    const fresh = el("button", null, "新しく作る");
    fresh.type = "button";
    fresh.onclick = () => renderForm(form, null, list);
    const buttons = el("div", "crow");
    buttons.append(check, save, ...(deck ? [fresh] : []));
    form.append(
      field("デッキ名", name, "半角英小文字・数字・ハイフン。対局の Player 名になる"),
      field("フォーマット", format),
      field("デッキリスト", text, "英語のカード名。MTG Arena の書き出しをそのまま貼れる。メイン 60 枚以上・サイドボード 15 枚まで"),
      field("プレイ方針（任意）", strategy),
      buttons, result);
    if (initial) show(initial);
  }

  return { render };
}
