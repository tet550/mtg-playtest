// 画面の文言の言語（日本語・英語）。辞書は i18n-ja.js・i18n-en.js（サーバーは web/ の直下のファイルだけ配るので、同じ階層に置く）。
// 言語は、覚えた選択（localStorage の mtgtable.lang）→ ブラウザの言語（ja* なら日本語）→ 英語 の順に決める。
// 卓に書くもの・AI に渡すもの（カード名・Note・カウンター・依頼の印）は訳さない。訳すのは画面に出す時だけ（design/i18n_plan.md）
import ja from "./i18n-ja.js";
import en from "./i18n-en.js";

export const DICTS = { ja, en };
export const LANGS = ["ja", "en"];
const KEY = "mtgtable.lang";

// stored: 覚えた選択、languages: ブラウザの言語（navigator.languages）
export function detect(stored, languages) {
  if (LANGS.includes(stored)) return stored;
  for (const l of languages || []) {
    const base = String(l).toLowerCase().split("-")[0];
    if (LANGS.includes(base)) return base;
  }
  return "en";
}

function stored() {
  try { return localStorage.getItem(KEY); } catch (_) { return null; }
}
const browserLanguages = () => (typeof navigator !== "undefined" && (navigator.languages || [navigator.language])) || [];

let current = detect(stored(), browserLanguages());
const listeners = [];

export const lang = () => current;

// key の文言。{name} は params で埋める。今の言語に無ければもう一方の言語、どちらにも無ければ key そのもの
export function t(key, params = {}) {
  const other = current === "ja" ? "en" : "ja";
  const s = DICTS[current][key] ?? DICTS[other][key] ?? key;
  return typeof s === "function" ? s(params) : s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

// HTML の印（data-i18n: 文、data-i18n-title: title、data-i18n-aria-label: aria-label）に今の言語の文言を入れる
export function applyDom(root = typeof document === "undefined" ? null : document) {
  if (!root) return;  // DOM の無い所（node のテスト）
  document.documentElement.lang = current;
  for (const e of root.querySelectorAll("[data-i18n]")) e.textContent = t(e.dataset.i18n);
  for (const e of root.querySelectorAll("[data-i18n-title]")) e.title = t(e.dataset.i18nTitle);
  for (const e of root.querySelectorAll("[data-i18n-aria-label]")) e.setAttribute("aria-label", t(e.dataset.i18nAriaLabel));
  for (const e of root.querySelectorAll("[data-lang]")) {
    e.classList.toggle("on", e.dataset.lang === current);
    e.setAttribute("aria-checked", String(e.dataset.lang === current));
  }
}

// 言語を変えて覚え、画面の印を入れ直し、描き直す人（onLang）を呼ぶ
export function setLang(l) {
  if (!LANGS.includes(l) || l === current) return;
  current = l;
  try { localStorage.setItem(KEY, l); } catch (_) { /* 覚えられなくても、このページの間は変わる */ }
  applyDom();
  for (const f of listeners) f(l);
}

export function onLang(f) { listeners.push(f); }
