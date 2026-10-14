export const $ = (id) => document.getElementById(id);
export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

// ドラッグの間はページの文字を選ばせない（なぞった先の文字が選ばれて青くなるのを防ぐ）。始めるときに残っている選択も消す
export const noSelect = (on) => {
  document.body.classList.toggle("nosel", on);
  if (on) getSelection()?.removeAllRanges();
};

// 待っている間の「…」（3つの点が順に跳ねる）
export const dots = () => {
  const d = el("span", "dots");
  d.setAttribute("aria-hidden", "true");
  d.append(el("i"), el("i"), el("i"));
  return d;
};

// はい・いいえの確認。window.confirm() はブラウザペインなどでダイアログを出さずに false を返すので、ページ内の <dialog> で聞く。
// 返り値は Promise<boolean>（ok を押したら true）
export function confirmDialog(title, ok, cancel = "やめる") {
  return new Promise((resolve) => {
    const dlg = el("dialog", "ask");
    const form = el("form", "askform");
    form.method = "dialog";
    let result = false;
    const yes = el("button", null, ok);
    yes.type = "button";
    yes.onclick = () => { result = true; dlg.close(); };
    const no = el("button", null, cancel);
    no.type = "button";
    no.onclick = () => dlg.close();
    const choices = el("div", "achoices");
    choices.append(yes);
    const buttons = el("div", "abuttons");
    buttons.append(no);
    form.append(el("div", "atitle", title), choices, buttons);
    dlg.append(form);
    dlg.onclose = () => { dlg.remove(); resolve(result); };
    document.body.append(dlg);
    dlg.showModal();
    no.focus();
  });
}
