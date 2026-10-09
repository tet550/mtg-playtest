// 通信と配信形式の違いはこのモジュールに閉じ込める。
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
  if (!r.ok) throw new Error(body.error || r.statusText);
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
  const mine = (game, seat) => !!key && key.game === game && key.seat === seat;
  const auth = (game, seat) => (mine(game, seat) && key.token ? { Authorization: `Bearer ${key.token}` } : undefined);
  async function postJSON(url, body, headers) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const error = new Error(data.error || r.statusText);
      error.status = r.status;
      error.stale = !!data.stale;
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
    async load(game, seat) {
      const url = gameURL(game, "timeline") + (isStatic ? "" : `?seat=${encodeURIComponent(seat)}`);
      const headers = auth(game, seat);
      // Log は judge の席と、対局している席（その Player に見せる形の Log）だけ
      const [timeline, log] = await Promise.all([
        getJSON(url, headers),
        seat === "judge" ? getJSON(gameURL(game, "log"))
          : mine(game, seat) ? getJSON(gameURL(game, "log") + `?seat=${encodeURIComponent(seat)}`, headers) : [],
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
    claim: (game, seat, token) => postJSON(gameURL(game, "claim"), { seat }, { Authorization: `Bearer ${token}` }),
    subscribe(game, onChange, onConnection) {
      if (isStatic) return () => {};
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
    imageURL: (name) => isStatic ? cards[name]?.image || "data:," : `/api/image?name=${encodeURIComponent(name)}`,
    symbolURL: (code) => isStatic ? `https://svgs.scryfall.io/card-symbols/${encodeURIComponent(code)}.svg`
      : `/api/symbol?s=${encodeURIComponent(code)}`,
    oracle(name) {
      if (!oracleCache.has(name)) {
        const request = isStatic ? scryfallOracle(name)
          : getJSON(`/api/oracle?name=${encodeURIComponent(name)}`).then((r) => r.text);
        oracleCache.set(name, request.catch(() => "").then((text) => text || "（オラクルを取得できなかった）"));
      }
      return oracleCache.get(name);
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
