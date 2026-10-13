// サーバーとの通信はこのモジュールに閉じ込める。
import { lang } from "./i18n.js";

async function getJSON(url, headers) {
  const r = await (headers ? fetch(url, { headers }) : fetch(url));
  const body = await r.json();
  if (!r.ok) {
    const error = new Error(body.error || r.statusText);
    error.status = r.status;
    throw error;
  }
  return body;
}

export function createSource() {
  let key = null;  // 対局する席 {game, seat, token}（GUI の対局）。鍵はその対局・席の要求にだけ付ける。
  // 手元のサイトのホストの席は token が無くてよい（手元のブラウザなので通る）
  const oracleCache = new Map();
  const gameURL = (game, resource) => `/api/games/${encodeURIComponent(game)}/${resource}`;
  let viewing = null;  // 再生（#/view）: {game}。終わった対局を、どの視点からも見る
  const mine = (game, seat) => !!key && key.game === game && key.seat === seat;
  const auth = (game, seat) => (mine(game, seat) && key.token ? { Authorization: `Bearer ${key.token}` } : undefined);
  async function postJSON(url, body, headers, method = "POST") {
    const r = await fetch(url, {
      method, headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const error = new Error(data.error || r.statusText);
      error.status = r.status;
      error.stale = !!data.stale;
      error.check = data.check || null;  // デッキの検査に通らなかったときの、行ごとの理由
      throw error;
    }
    return data;
  }
  return {
    config: () => getJSON("/api/config"),
    setKey(value) { key = value; },
    games: () => getJSON("/api/games"),
    setViewing(value) { viewing = value; },
    async load(game, seat) {
      const url = gameURL(game, "timeline") + `?seat=${encodeURIComponent(seat)}`;
      const headers = auth(game, seat);
      // Log は judge の席と、対局している席・再生している席（その Player に見せる形の Log）だけ
      const seatLog = mine(game, seat) || (viewing && viewing.game === game);
      const [timeline, log] = await Promise.all([
        getJSON(url, headers),
        seat === "judge" ? getJSON(gameURL(game, "log"))
          : seatLog ? getJSON(gameURL(game, "log") + `?seat=${encodeURIComponent(seat)}`, headers) : [],
      ]);
      return { timeline, log };
    },
    // 止める場所（非公開。その席の鍵が要る）
    getStops: (game, seat) => getJSON(gameURL(game, "stops") + `?seat=${encodeURIComponent(seat)}`, auth(game, seat)),
    // 席の Player として書く（request / declare / answer / stops）。409（見ていた盤面より進んでいた）は error.stale
    post: (game, action, body) => postJSON(gameURL(game, action), body, auth(game, body.seat)),
    // 履歴（手元のサイト。ホストだけ）
    history: () => getJSON("/api/history"),
    access: (game) => getJSON(`/api/games/${encodeURIComponent(game)}/access`),
    deleteGame: (game) => postJSON(`/api/games/${encodeURIComponent(game)}`, {}, {}, "DELETE"),
    // デッキ（手元のサイト。ホストだけ）
    decks: () => getJSON("/api/decks"),
    deck: (id) => getJSON(`/api/decks/${encodeURIComponent(id)}`),
    checkDeck: (text, format) => postJSON("/api/decks/check", { text, format }),
    saveDeck: (id, body) => (id ? postJSON(`/api/decks/${encodeURIComponent(id)}`, body, {}, "PUT")
      : postJSON("/api/decks", body)),
    deleteDeck: (id) => postJSON(`/api/decks/${encodeURIComponent(id)}`, {}, {}, "DELETE"),
    // 対局を作る・招待（手元のサイト）。招待に着くのはゲスト（デッキを貼るか同梱のデッキ）
    aiDecks: () => getJSON("/api/ai-decks"),
    createGame: (body) => postJSON("/api/games", body),
    invites: () => getJSON("/api/invites"),
    invite: (id, token) => getJSON(`/api/invites/${encodeURIComponent(id)}?token=${encodeURIComponent(token)}`),
    joinInvite: (id, token, deck) => postJSON(`/api/invites/${encodeURIComponent(id)}/join`, { token, ...deck }),
    renewInvite: (id) => postJSON(`/api/invites/${encodeURIComponent(id)}/renew`, {}),
    cancelInvite: (id) => postJSON(`/api/invites/${encodeURIComponent(id)}`, {}, {}, "DELETE"),
    subscribe(game, onChange, onConnection) {
      const events = new EventSource(gameURL(game, "events"));
      events.onopen = () => onConnection(true);
      events.onerror = () => onConnection(false);
      events.onmessage = (event) => onChange(JSON.parse(event.data));
      return () => {
        events.onopen = events.onerror = events.onmessage = null;
        events.close();
        onConnection(false);
      };
    },
    // 日本語の画面では日本語版の画像（無ければ英語）
    imageURL: (name) => `/api/image?name=${encodeURIComponent(name)}${lang() === "ja" ? "&lang=ja" : ""}`,
    // カード名の日本語 {names: {英語名: 日本語名}, pending: [まだ引けていない名前]}。画面が知っているカードの名前だけを聞く
    // （URL が長くなりすぎないよう、呼ぶ側が 40 件ほどに分ける）
    names: (list) => getJSON(`/api/names?lang=ja&${list.map((n) => `n=${encodeURIComponent(n)}`).join("&")}`),
    symbolURL: (code) => `/api/symbol?s=${encodeURIComponent(code)}`,
    // カードの詳細 {text, lang, oracle}。日本語の画面で日本語版があれば text は日本語版の文（印刷された文）、
    // oracle は英語のオラクル。取れなければ text は ""（知らせの文は画面の言語で、描く側が出す）
    oracle(name) {
      const l = lang();
      const key = `${l}:${name}`;
      if (!oracleCache.has(key)) {
        const request = getJSON(`/api/oracle?name=${encodeURIComponent(name)}${l === "ja" ? "&lang=ja" : ""}`)
          .then((r) => ({ text: r.text || "", lang: r.lang || "en", oracle: r.oracle || "" }));
        oracleCache.set(key, request.catch(() => ({ text: "", lang: "en", oracle: "" })));
      }
      return oracleCache.get(key);
    },
  };
}

// 古い成功・失敗はどちらも破棄する。対局・席の切替と更新通知で共用する。
export function latestLoader(source) {
  let revision = 0;
  return async (game, seat) => {
    const request = ++revision;
    try {
      const result = await source.load(game, seat);
      return request === revision ? result : null;
    } catch (error) {
      if (request === revision) throw error;
      return null;
    }
  };
}
