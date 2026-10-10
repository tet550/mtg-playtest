// 通信と配信形式の違いはこのモジュールに閉じ込める。
import { lang } from "./i18n.js";

// 静的書き出し（サーバー無し）の日本語版のカードの文: ブラウザから Scryfall の日本語版の印刷を引く（文のある最新の1枚）
async function scryfallJapanese(name) {
  const q = encodeURIComponent(`!"${name.replace(/"/g, "")}" lang:ja`);
  const r = await fetch(`https://api.scryfall.com/cards/search?q=${q}&unique=prints&order=released&dir=desc`,
    { headers: { Accept: "application/json" } });
  if (!r.ok) return "";
  const card = ((await r.json()).data || []).find((c) => c.printed_text || (c.card_faces || []).some((f) => f.printed_text));
  if (!card) return "";
  const faces = card.card_faces && card.card_faces.some((f) => f.printed_name) ? card.card_faces : [card];
  return faces.map((f) => [`<${f.printed_name || f.name}> ${f.mana_cost || ""}`.trim(), f.printed_type_line || f.type_line || "",
    f.printed_text || ""].filter(Boolean).join("\n")).join("\n----\n");
}
async function scryfallOracle(name) {
  const r = await fetch(`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}`,
    { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(r.statusText);
  const card = await r.json();
  const faces = card.card_faces && card.card_faces.some((f) => f.oracle_text !== undefined) ? card.card_faces : [card];
  // "Treasure" が "Dinosaur // Treasure" に当たるときなどは、名前が一致する面だけ
  const hit = faces.filter((f) => f.name.toLowerCase() === name.toLowerCase());
  return (hit.length ? hit : faces).map((f) => {
    const lines = [`<${f.name}> ${f.mana_cost || ""}`.trim(), f.type_line || card.type_line || ""];
    if (f.oracle_text) lines.push(f.oracle_text);
    const pt = f.power !== undefined ? `${f.power}/${f.toughness}` : (f.loyalty ? `loyalty ${f.loyalty}` : (f.defense ? `defense ${f.defense}` : ""));
    if (pt) lines.push(pt);
    return lines.join("\n");
  }).join("\n\n");
}

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

export function createSource(isStatic) {
  let cards = {};
  let key = null;  // 対局する席 {game, seat, token}（GUI の対局）。鍵はその対局・席の要求にだけ付ける。
  // 公開のサーバーで、席を取った後は token が無くてよい（所有者の鍵の Cookie で通る）
  const oracleCache = new Map();
  const gameURL = (game, resource) => isStatic
    ? `data/${encodeURIComponent(game)}/${resource}.json`
    : `/api/games/${encodeURIComponent(game)}/${resource}`;
  let viewing = null;  // 観戦・再生（#/view）: {game, share}。共有 URL の鍵 share をその対局の読み取りに付ける
  const mine = (game, seat) => !!key && key.game === game && key.seat === seat;
  const shareQ = (game, sep) => (viewing && viewing.game === game && viewing.share
    ? `${sep}share=${encodeURIComponent(viewing.share)}` : "");
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
    isStatic,
    async init() { if (isStatic) cards = await getJSON("data/cards.json"); },
    config: () => (isStatic ? Promise.resolve({ play: false }) : getJSON("/api/config")),
    setKey(value) { key = value; },
    games: () => getJSON(isStatic ? "data/games.json" : "/api/games"),
    setViewing(value) { viewing = value; },
    async load(game, seat) {
      const url = gameURL(game, "timeline") + (isStatic ? "" : `?seat=${encodeURIComponent(seat)}${shareQ(game, "&")}`);
      const headers = auth(game, seat);
      // Log は judge の席と、対局している席・観戦している席（その Player に見せる形の Log）だけ
      const seatLog = mine(game, seat) || (viewing && viewing.game === game);
      const [timeline, log] = await Promise.all([
        getJSON(url, headers),
        seat === "judge" ? getJSON(gameURL(game, "log") + (isStatic ? "" : shareQ(game, "?")))
          : seatLog ? getJSON(gameURL(game, "log") + `?seat=${encodeURIComponent(seat)}${shareQ(game, "&")}`, headers) : [],
      ]);
      return { timeline, log };
    },
    // 止める場所（非公開。その席の鍵が要る）
    getStops: (game, seat) => getJSON(gameURL(game, "stops") + `?seat=${encodeURIComponent(seat)}`, auth(game, seat)),
    // 席の Player として書く（request / declare / answer / stops）。409（見ていた盤面より進んでいた）は error.stale
    post: (game, action, body) => postJSON(gameURL(game, action), body, auth(game, body.seat)),
    // 公開のサーバー: 復元の鍵を作り直す（別の端末で開く URL 用）・復元の鍵でこの端末を同じ所有者に戻す
    recovery: () => postJSON("/api/me/recovery", {}),
    recover: (token) => postJSON("/api/me/recover", { token }),
    // 履歴・公開の対局・共有 URL（公開のサーバー）
    history: () => getJSON("/api/history"),
    publicGames: (deck, before) => getJSON("/api/public?" + new URLSearchParams(
      Object.entries({ deck, before }).filter(([, v]) => v))),
    access: (game, share) => getJSON(`/api/games/${encodeURIComponent(game)}/access`
      + (share ? `?share=${encodeURIComponent(share)}` : "")),
    shares: (game) => getJSON(`/api/games/${encodeURIComponent(game)}/shares`),
    createShare: (game, view) => postJSON(`/api/games/${encodeURIComponent(game)}/shares`, { view }),
    revokeShare: (game, id) => postJSON(`/api/games/${encodeURIComponent(game)}/shares/${encodeURIComponent(id)}`, {}, {}, "DELETE"),
    consent: (game) => postJSON(`/api/games/${encodeURIComponent(game)}/consent`, {}),
    hide: (game) => postJSON(`/api/games/${encodeURIComponent(game)}/hide`, {}),
    // デッキ（公開のサーバー。自分のデッキだけ）
    decks: () => getJSON("/api/decks"),
    deck: (id) => getJSON(`/api/decks/${encodeURIComponent(id)}`),
    checkDeck: (text, format) => postJSON("/api/decks/check", { text, format }),
    saveDeck: (id, body) => (id ? postJSON(`/api/decks/${encodeURIComponent(id)}`, body, {}, "PUT")
      : postJSON("/api/decks", body)),
    deleteDeck: (id) => postJSON(`/api/decks/${encodeURIComponent(id)}`, {}, {}, "DELETE"),
    // 対局を作る・招待（公開のサーバー）
    aiDecks: () => getJSON("/api/ai-decks"),
    createGame: (body) => postJSON("/api/games", body),
    invites: () => getJSON("/api/invites"),
    invite: (id, token) => getJSON(`/api/invites/${encodeURIComponent(id)}?token=${encodeURIComponent(token)}`),
    joinInvite: (id, token, deck) => postJSON(`/api/invites/${encodeURIComponent(id)}/join`, { token, deck }),
    renewInvite: (id) => postJSON(`/api/invites/${encodeURIComponent(id)}/renew`, {}),
    cancelInvite: (id) => postJSON(`/api/invites/${encodeURIComponent(id)}`, {}, {}, "DELETE"),
    claim: (game, seat, token) => postJSON(gameURL(game, "claim"), { seat }, { Authorization: `Bearer ${token}` }),
    subscribe(game, onChange, onConnection) {
      if (isStatic) return () => {};
      const events = new EventSource(gameURL(game, "events") + shareQ(game, "?"));
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
    imageURL: (name) => isStatic ? (lang() === "ja" && cards[name]?.ja?.image) || cards[name]?.image || "data:,"
      : `/api/image?name=${encodeURIComponent(name)}${lang() === "ja" ? "&lang=ja" : ""}`,
    // カード名の日本語 {names: {英語名: 日本語名}, pending: [まだ引けていない名前]}。画面が知っているカードの名前だけを聞く
    // （URL が長くなりすぎないよう、呼ぶ側が 40 件ほどに分ける）
    names(list) {
      if (isStatic) {
        return Promise.resolve({ names: Object.fromEntries(list.filter((n) => cards[n]?.ja?.name).map((n) => [n, cards[n].ja.name])), pending: [] });
      }
      return getJSON(`/api/names?lang=ja&${list.map((n) => `n=${encodeURIComponent(n)}`).join("&")}`);
    },
    symbolURL: (code) => isStatic ? `https://svgs.scryfall.io/card-symbols/${encodeURIComponent(code)}.svg`
      : `/api/symbol?s=${encodeURIComponent(code)}`,
    // カードの詳細 {text, lang, oracle}。日本語の画面で日本語版があれば text は日本語版の文（印刷された文）、
    // oracle は英語のオラクル。取れなければ text は ""（知らせの文は画面の言語で、描く側が出す）
    oracle(name) {
      const l = lang();
      const key = `${l}:${name}`;
      if (!oracleCache.has(key)) {
        let request;
        if (!isStatic) {
          request = getJSON(`/api/oracle?name=${encodeURIComponent(name)}${l === "ja" ? "&lang=ja" : ""}`)
            .then((r) => ({ text: r.text || "", lang: r.lang || "en", oracle: r.oracle || "" }));
        } else if (l === "ja") {
          request = Promise.all([scryfallJapanese(name).catch(() => ""), scryfallOracle(name).catch(() => "")])
            .then(([ja, en]) => (ja ? { text: ja, lang: "ja", oracle: en } : { text: en, lang: "en", oracle: "" }));
        } else {
          request = scryfallOracle(name).then((text) => ({ text, lang: "en", oracle: "" }));
        }
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
