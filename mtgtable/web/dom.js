export const $ = (id) => document.getElementById(id);
export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};

// 待っている間の「…」（3つの点が順に跳ねる）
export const dots = () => {
  const d = el("span", "dots");
  d.setAttribute("aria-hidden", "true");
  d.append(el("i"), el("i"), el("i"));
  return d;
};
