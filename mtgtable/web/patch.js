// 描き直しを、今の DOM への差分として当てる。組み立ての関数は毎回その時点の画面の木を新しく作り、ここで今の要素に
// 合わせる。同じものと分かる要素は使い回すので、選んだ文字・フォーカス・打ちかけの文字・開いた折りたたみ・スクロール・
// ドラッグ中のカードが更新で消えない。
//
// 対応づけ: data-key、無ければ data-ids（カード）で同じ親の中の同じものを探す。どちらも無い子は、同じ親の中で
// 同じ種類（タグと最初のクラス）が出てくる順で対応づける。
// イベントはプロパティ（el.onclick = …）で付ける約束で、毎回新しい方に付け替える。addEventListener で付けたものは
// 付け替えられないので、使い回す要素には付けない（描き直しで消えない親で受ける）。
// 付け替えたイベントの関数は、新しく作った方の要素を握っている。自分の要素に触るときは e.currentTarget を使う。

const EVENTS = ["onclick", "oncontextmenu", "onchange", "oninput", "onkeydown", "onerror", "onload", "onpointerdown"];
// 画面の側で付け外しするクラス・属性（組み立ての木には無い）。差分で消さない
const KEEP_CLASSES = ["dragging", "dropover"];
const KEEP_ATTRS = ["open"];  // details の開閉は見ている人のもの

const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute("data-key") || n.getAttribute("data-ids") : null);
const kindOf = (n) => (n.nodeType === 1 ? `${n.namespaceURI}:${n.localName}.${(n.getAttribute("class") || "").split(" ")[0]}` : `#${n.nodeType}`);

// parent の子を next（新しく作った節の並び）と同じにする。next の節は使い回されなければそのまま入る
export function patch(parent, next) {
  const keyed = new Map();
  const loose = new Map();
  for (const n of parent.childNodes) {
    const k = keyOf(n);
    if (k) keyed.set(k, n);
    else {
      const kind = kindOf(n);
      if (!loose.has(kind)) loose.set(kind, []);
      loose.get(kind).push(n);
    }
  }
  // 先に対応を決めて、使わない節を外す（間に残っていると、使い回す節を挿し直すことになり、フォーカスが外れる）
  const pairs = [];
  const used = new Set();
  for (let n of next) {
    if (n == null || n === false) continue;
    if (typeof n === "string") n = document.createTextNode(n);
    const k = keyOf(n);
    let old = null;
    if (k) {
      old = keyed.get(k) || null;
      if (old) keyed.delete(k);
    } else {
      const list = loose.get(kindOf(n));
      old = list && list.length ? list.shift() : null;
    }
    // キーが同じでも、タグが違えば作り直す
    if (old && (old.nodeType !== n.nodeType || (n.nodeType === 1 && (old.localName !== n.localName || old.namespaceURI !== n.namespaceURI)))) old = null;
    if (old) used.add(old);
    pairs.push([old, n]);
  }
  for (const n of [...parent.childNodes]) if (!used.has(n)) n.remove();
  // 並べる。使い回す節は、並びが変わったときだけ動かす
  let at = parent.firstChild;
  for (const [old, n] of pairs) {
    const node = old || n;
    if (old) morph(old, n); else remember(n);
    if (node === at) at = at.nextSibling;
    else parent.insertBefore(node, at);
  }
}

// 入力欄に、組み立てが最後に入れた値を覚えておく（それから変わっていれば、見ている人が書き換えた）
function remember(n) {
  if (n.nodeType !== 1) return;
  const inputs = n.matches("input, textarea") ? [n] : n.querySelectorAll("input, textarea");
  for (const x of inputs) { x.__value = x.value; x.__checked = x.checked; }
}

function morph(a, b) {
  if (a.nodeType !== 1) {
    if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue;
    return;
  }
  // 属性。style は CSSOM で書く（サイトの CSP は style 属性を setAttribute で書くのを止める）
  for (const { name, value } of [...b.attributes]) {
    if (name === "class" || name === "style") continue;
    if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  }
  for (const { name } of [...a.attributes]) {
    if (name !== "class" && name !== "style" && !b.hasAttribute(name) && !KEEP_ATTRS.includes(name)) a.removeAttribute(name);
  }
  if (a.style && a.style.cssText !== b.style.cssText) a.style.cssText = b.style.cssText;
  const keep = KEEP_CLASSES.filter((c) => a.classList.contains(c));
  const cls = [b.getAttribute("class") || "", ...keep.filter((c) => !b.classList.contains(c))].join(" ").trim();
  if ((a.getAttribute("class") || "") !== cls) {
    if (cls) a.setAttribute("class", cls); else a.removeAttribute("class");
  }
  for (const on of EVENTS) if (a[on] !== b[on]) a[on] = b[on];
  // 入力欄: 組み立てが値を変えたときだけ入れる（打ちかけ・変換中の文字はそのまま）
  if (a.localName === "input" || a.localName === "textarea") {
    if (b.value !== a.__value) { if (a.value !== b.value) a.value = b.value; a.__value = b.value; }
    if (b.checked !== a.__checked) { a.checked = b.checked; a.__checked = b.checked; }
    return;
  }
  patch(a, [...b.childNodes]);
}
