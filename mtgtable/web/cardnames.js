// カード名の表示の言語。卓・審判に送る文・ui.names は英語名のまま（英語名がカードの識別子）。日本語の画面では、
// 表示する時だけ日本語名に置き換える（日本語版の無いカードは英語名のまま）。Note は書かれたまま（置き換えない）。
// 名前の表は、画面が知っているカードの名前だけをサーバーに聞いて作る（相手の隠れたカードの名前を聞かない）。
import { lang } from "./i18n.js";

const ja = new Map();      // 英語名 → 日本語名
const asked = new Set();   // 聞いた名前（日本語版の無いカードも含む。聞き直さない）
const RETRY_MS = 1500;     // サーバーが引いている途中の名前（pending）を聞き直す間隔
const RETRIES = 30;        // 1回の要求で引けるのは数枚なので、大きな対局の初回は何度か聞き直す
const CHUNK = 40;          // 1回に聞く名前の数（URL の長さ）

export function displayName(name) {
  return (lang() === "ja" && name && ja.get(name)) || name;
}

// 文の中の <英語名> を <日本語名> に（表示用。文そのものは変えない）
export function localize(text) {
  if (lang() !== "ja" || !text || !ja.size) return text;
  return text.replace(/<([^<>\n]+)>/g, (m, n) => (ja.has(n) ? `<${ja.get(n)}>` : m));
}

// names の日本語名を聞く。新しく分かったら onUpdate（描き直し）を呼ぶ。英語の画面では何もしない
export function createCardNames(source, onUpdate) {
  let busy = false;
  let queued = new Set();
  // 40 件ずつ聞き、分かった分から描き直す。サーバーが引けなかった名前（pending）は少し待って聞き直す
  async function ask(list, tries) {
    const pending = [];
    for (let i = 0; i < list.length; i += CHUNK) {
      const r = await source.names(list.slice(i, i + CHUNK)).catch(() => ({ names: {}, pending: [] }));
      const found = Object.entries(r.names || {});
      for (const [en, name] of found) ja.set(en, name);
      if (found.length && lang() === "ja") onUpdate();
      pending.push(...(r.pending || []).filter((n) => !ja.has(n)));
    }
    if (pending.length && tries > 0) await new Promise((ok) => setTimeout(ok, RETRY_MS)).then(() => ask(pending, tries - 1));
  }
  async function drain() {
    if (busy) return;
    busy = true;
    try {
      while (queued.size) {
        const list = [...queued];
        queued = new Set();
        await ask(list, RETRIES);
      }
    } finally {
      busy = false;
    }
  }
  return {
    want(names) {
      if (lang() !== "ja") return;
      for (const n of names) if (n && !asked.has(n)) { asked.add(n); queued.add(n); }
      if (queued.size) drain();
    },
  };
}
