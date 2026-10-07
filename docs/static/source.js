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

async function getJSON(url) {
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

export function createSource(isStatic) {
  let cards = {};
  const oracleCache = new Map();
  const gameURL = (game, resource) => isStatic
    ? `data/${encodeURIComponent(game)}/${resource}.json`
    : `/api/games/${encodeURIComponent(game)}/${resource}`;
  return {
    isStatic,
    async init() { if (isStatic) cards = await getJSON("data/cards.json"); },
    games: () => getJSON(isStatic ? "data/games.json" : "/api/games"),
    async load(game, seat) {
      const url = gameURL(game, "timeline") + (isStatic ? "" : `?seat=${encodeURIComponent(seat)}`);
      const [timeline, log] = await Promise.all([
        getJSON(url), seat === "judge" ? getJSON(gameURL(game, "log")) : [],
      ]);
      return { timeline, log };
    },
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
