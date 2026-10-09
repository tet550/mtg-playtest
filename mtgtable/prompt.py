"""AI のプロンプト（Claude API に送る形）を作り、返答を卓に適用する。

役割は3つ（mode）:

    intent  Player（AI の席）。何をするかを決めて意図を文で返す。卓は動かさない          system: player_intent.md + rules.md
    judge   審判。Player の意図・回答を Batch に直し、ルールを確かめて卓に書く。質問もする  system: judge.md + リファレンス
    direct  Player が Batch を直接書く（審判を通さない、前の形。比べるために残す）        system: player.md + リファレンス

Player（人間の GUI・AI）は卓を動かさず、依頼・回答と、盤面を変えない宣言だけを書く（play.seat_batch）。
審判とのやりとりは卓の宣言（intent / ask / answer / ruled）として記録し、play.waiting_on が「次に誰が動くか」を決める。

手動なら、`next` がプロンプトをファイルに書き出し、人がモデルに渡し、返答を `answer` に渡す。
自動なら、`auto`（llm.py）が同じ `build()` と `apply_response()` で OpenAI の API を呼ぶ（request.json がその本文）。

プロンプト・キャッシュのため、変わらない部分を前に、変わる部分を後ろに置く:

    system[0]  役割の指示とリファレンス              全対局で同じ
    system[1]  席（Player）か対局（審判）の固定情報    対局の間は同じ
    messages   今回の状況                             毎回変わる

固定部分には時刻・cursor・version など進行で変わる値を入れない（1バイトでも変わるとキャッシュが外れる）。
毎回のメッセージは1通だけ（前のやりとりを積まない）。Player の計画は memo で持ち越す。
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Optional

from . import carddb, info, play
from .operations import summarize_op
from .render import render_view
from .store import GameStore, StaleCursor, _dump

RECENT = 40  # 審判に見せる最近の記録の上限
RECENT_MIN = 8  # 前回の処理の後が少なくても、これだけは見せる（直前の流れ）

HERE = pathlib.Path(__file__).with_name("prompts")
REPO = pathlib.Path(__file__).resolve().parents[1]
REFERENCES = REPO / ".claude" / "skills" / "mtg-playtest" / "references"
STRATEGY = REPO / "decklists" / "strategy"
JUDGE = play.JUDGE
MODES = ("intent", "judge", "direct")


# ---------------------------------------------------------------- 固定部分

def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _cli_reference() -> str:
    """cli.md の「id と名前」から後（コマンドの一覧は要らない）。"""
    text = (REFERENCES / "cli.md").read_text(encoding="utf-8")
    return "# mtgtable の書式（cli.md より）\n\n" + text[text.index("## id と名前"):].strip()


def role_system(mode: str) -> str:
    """役割ごとに全対局で同じ部分。ファイルを直さない限り同じバイト列になる。"""
    if mode == "intent":
        parts = [_read(HERE / "player_intent.md"), "---\n\n# リファレンス", _read(REFERENCES / "rules.md")]
    elif mode in ("judge", "direct"):
        head = "judge.md" if mode == "judge" else "player.md"
        parts = [_read(HERE / head), "---\n\n# リファレンス", _cli_reference(),
                 _read(REFERENCES / "patterns.md"), _read(REFERENCES / "rules.md")]
    else:
        raise ValueError("unknown mode %r" % mode)
    return "\n\n".join(parts) + "\n"


def common_system() -> str:  # 前の名前（direct）
    return role_system("direct")


def _deck_data(state, seat: str) -> Optional[dict]:
    path = state.meta.get("decks", {}).get(seat, {}).get("oracle")
    if not path or not pathlib.Path(path).exists():
        return None
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def seat_system(store: GameStore, seat: str) -> str:
    """Player の席の固定情報。初期状態から作るので、対局の間は同じバイト列になる。"""
    state = store.initial()
    if seat not in state.players:
        raise ValueError("unknown seat %r" % seat)
    others = [p for p in state.player_order if p != seat]
    deck = state.meta.get("decks", {}).get(seat, {})
    out = ["# あなたの席",
           "あなたは %s（デッキ: %s）。相手は %s。" % (seat, deck.get("name", "?"), ", ".join(others) or "いない（一人回し）"),
           "初期ライフ・先攻などは盤面を見る。"]
    data = _deck_data(state, seat)
    if data:
        out += ["", "# あなたのデッキ（オラクル）", carddb.format_deck(data, sideboard=False).strip()]
    name = deck.get("name")
    if deck.get("strategy") is not None:
        # サイトで登録したデッキ: 登録したプレイ方針だけ（同じ名前の decklists/strategy/ のファイルは別のデッキのもの）
        if deck["strategy"].strip():
            out += ["", "# 戦略メモ（デッキ登録時のプレイ方針）", deck["strategy"].strip()]
    elif name and STRATEGY.exists():
        for p in sorted(STRATEGY.glob("%s*.md" % name)):
            if p.stem == name or p.stem.startswith(name + "-"):
                out += ["", "# 戦略メモ: %s" % p.name, _read(p)]
    return "\n".join(out) + "\n"


def game_system(store: GameStore) -> str:
    """審判の固定情報: 席と両者のデッキのオラクル（審判は全部を知ってよい）。"""
    state = store.initial()
    out = ["# この対局", "Player: " + ", ".join(
        "%s（デッキ: %s）" % (p, state.meta.get("decks", {}).get(p, {}).get("name", "?")) for p in state.player_order)]
    for p in state.player_order:
        data = _deck_data(state, p)
        if data:
            out += ["", "# %s のデッキ（オラクル）" % p, carddb.format_deck(data, sideboard=False).strip()]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 毎回変わる部分

def quote(text: str, indent: str = "      | ") -> str:
    """Player などが書いた文をプロンプトに埋め込む形に。2行目からは字下げして、行頭の「# 依頼」のような
    見出しでプロンプトの区切りを装えないようにする。"""
    return ("\n" + indent).join(str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def _write_memo(store: GameStore, seat: str, memo) -> None:
    if isinstance(memo, str):
        memo_path(store, seat).write_text(memo.strip()[:play.MAX_MEMO] + "\n", encoding="utf-8")


def _entry_line(e: dict, viewer: Optional[str] = None) -> str:
    """log の1件を1行に。viewer（Player）が見る行では、他の Player の依頼・回答・質問の中身を伏せる。"""
    hidden = [op for op in e["act"] if op["op"] == "declare" and op.get("kind") in play.PRIVATE_KINDS
              and viewer is not None and op.get("player", e["actor"]) != viewer]
    if hidden and len(hidden) == len(e["act"]):
        return "%4d %-5s （審判とのやりとり）" % (e["seq"], e["actor"] or "judge")
    text = e["label"] or "; ".join(summarize_op(op) for op in e["act"])
    if e.get("proc"):
        text = "[%s] %s" % (e["proc"], text)
    if e.get("proxy_by"):
        text = "[proxy by %s] %s" % (e["proxy_by"], text)
    decl = [s["op"] for s in e["steps"] if s["op"]["op"] == "declare" and s["op"].get("kind") in
            ("intent", "ask", "answer", "ruled")]
    for st in e["steps"]:  # 公開・見る の結果（どのカードだったか）は op の要約に出ないので、見てよい Player には出す
        op = st["op"]
        to = op.get("to", "all")
        if op["op"] == "reveal" and (viewer is None or to == "all" or viewer in ([to] if isinstance(to, str) else to))                 or op["op"] == "look" and (viewer is None or op.get("player", e["actor"]) == viewer):
            text += "".join("  → %s" % ev for ev in st.get("events", []))
    for d in decl:  # 審判とのやりとりは中身も出す（依頼・回答・質問は本人と審判だけ。ruled は公開）
        if viewer is not None and d["kind"] in play.PRIVATE_KINDS and d.get("player", e["actor"]) != viewer:
            continue
        if d.get("text") and d["text"] not in text:
            text += "  [%s %s: %s]" % (d["kind"], d.get("player", ""), d["text"])
    return "%4d %-5s %s" % (e["seq"], e["actor"] or "judge", quote(text))


def _opponent_oracle(state, seat: str) -> str:
    """seat が知っている、自分のデッキに無いカードのオラクル（相手のカード）。トークンは view の definition で分かるので引かない
    （名前で引くと、同名のカードに当たる）。"""
    data = _deck_data(state, seat)
    own = set(data["cards"]) if data else set()
    names = sorted({c.name for cid, c in state.cards.items()
                    if state.zones[c.zone].kind != "sideboard" and c.name and c.name not in own and not c.token
                    and info.knows_identity(state, seat, cid)})
    out = []
    for n in names:
        rec = carddb.lookup(n, offline=True)
        out.append(carddb.format_card_compact(rec) if rec else "<%s>（オラクルなし）" % n)
    return "\n".join(out)


def _situation(state, me: Optional[str]) -> list:
    t = state.turn
    wait = play.waiting_on(state)
    who = "あなた" if wait == me else {None: "誰でもない（決着）", JUDGE: "審判"}.get(wait, wait)
    return ["# 今の状況", "T%d %s/%s。アクティブ %s、優先権 %s。待たれているのは %s。"
            % (t.turn, t.phase, t.step, t.active or "-", t.priority or "-", who)]


def _error(error: Optional[str]) -> list:
    return ["", "# 前回のあなたの返答は適用できなかった", error,
            "失敗した Act とそれより後は適用されていない。今の盤面から書き直す。"] if error else []


def user_message(store: GameStore, seat: str, error: Optional[str] = None, mode: str = "direct") -> str:
    """Player（intent / direct）への今回のメッセージ。seat が見てよいものだけ。"""
    state = store.load()
    since = play.last_own(store, seat)
    cur = store.cursor()
    wait = play.waiting_on(state)
    out = _situation(state, seat) + _error(error)
    if state.turn.turn == 0 and play.mulligans(state, seat):
        n = play.mulligans(state, seat)
        out += ["マリガン %d 回。キープすると、ゲーム開始の前に審判が、ライブラリーの一番下に置く %d 枚を聞く。" % (n, n)]
    out += ["", "# 前回あなたが動いた後に起きたこと"]
    out += [_entry_line(e, seat) for e in store.read_log()[since:cur]] or ["（なし）"]
    memo = memo_path(store, seat)
    out += ["", "# あなたの memo", _read(memo) if memo.exists() else "（なし）"]
    out += ["", "# 止める場所（あなたの非公開の設定。変えるなら返答に stops）", _stops_text(store, seat)]
    view = info.player_view(state, seat, library=True, graveyard=True)
    out += ["", "# 盤面（あなたの view）", render_view(view).rstrip()]
    opp = _opponent_oracle(state, seat)
    if opp:
        out += ["", "# 相手のカードのオラクル（デッキ一覧に無いもの）", opp]
    back = play.resume(state, seat)
    if back:
        out += ["", "# 途中で止まったあなたの計画（依頼 #%d の残り。相手の割り込みなどで止まった。続けるなら、直して request で"
                    "送り直す）" % back["of"]]
        out += ["行 %d: %s" % (i, quote(str(x.get("text", "")))) for i, x in enumerate(back["lines"], 1)]
        out.append("その後: %s" % play._then_text(back["then"]))
        if back["unknown"]:
            out.append("（審判がどこまで処理したか分からない。盤面で済んだ行を確かめて外す）")
    asks = [d for d in play.requests(state)["asks"] if d.player == seat]
    if asks:
        out += ["", "# 審判からの質問（answer で答える）"]
        for d in asks:
            out.append("#%d %s%s" % (d.seq, quote(d.text), "\n選択肢: " + " / ".join(quote(c) for c in d.choices)
                                     if d.choices else ""))
            if d.cards:
                out.append("候補のカード（%s 枚を cards で選ぶ）: %s" % (
                    "%d" % d.pick[0] if d.pick[0] == d.pick[1] else "%d〜%d" % tuple(d.pick),
                    ", ".join("%s <%s>" % (c, state.cards[c].name) for c in d.cards)))
    if mode == "intent":
        task = ("審判の質問に answer で答える。" if asks else
                "あなたの番。何をするかを決め、JSON のコード・ブロックで返す（request・declare・answer のどれか1つ）。") if wait == seat else \
            "今は %s の番。この時点であなたが行うことは無いので、何も返さなくてよい。" % (wait or "誰でもない")
    else:
        task = ("あなたの番。次に判断が要る所まで Batch を書き、最後に JSON のコード・ブロックで返す。" if wait == seat else
                "今は %s の番。あなたがこの時点で割り込んで行うことがあれば Batch を書き、無ければ "
                '{"batch": null} を返す。' % (wait or "誰でもない"))
    out += ["", "# 依頼", task]
    return "\n".join(out) + "\n"


STOP_JA = {"spell": "呪文・能力を積んだとき", "attack": "攻撃したとき"}


def _stops_text(store: GameStore, pid: str) -> str:
    codes = play.get_stops(store, pid)
    if not codes:
        return "（設定なし）"
    word = {"own": "自分のターン", "opp": "相手のターン"}
    return ", ".join("%s の %s" % (word.get(c.split(":")[0], c), STOP_JA.get(c.split(":")[1], c.split(":")[1]))
                     for c in codes)


def judge_message(store: GameStore, error: Optional[str] = None) -> str:
    """審判への今回のメッセージ: 処理する依頼・最近の記録・全情報の盤面。"""
    state = store.load()
    req = play.requests(state)
    decls = {d.seq: d for d in state.declarations}
    out = _situation(state, JUDGE) + _error(error)
    humans = play.seats(store)
    out += ["", "# Player", ", ".join("%s: %s" % (p, "人間（GUI）" if p in humans
                                                  else "AI（止める場所の設定と依頼の指示どおりに止める）")
                                      for p in state.player_order)]
    out += ["", "# 処理する依頼"]
    for d in req["pending"]:
        if d.kind == "mulligan":
            out.append("#%d %s mulligan: マリガン（手札を全部ライブラリーに戻してシャッフルし、%d 枚引き直す。下に置くのはキープの後）"
                       % (d.seq, d.player, state.meta.get("hand", 7)))
            continue
        line = "#%d %s %s: %s" % (d.seq, d.player, d.kind, quote(d.text) if d.text else "（文なし）")
        if d.kind == "answer":  # 何への回答か
            ask = max((x for x in decls.values() if x.kind == "ask" and x.player == d.player and x.seq < d.seq),
                      key=lambda x: x.seq, default=None)
            if ask:
                line += "\n    （質問 #%d: %s）" % (ask.seq, quote(ask.text))
            if (ask and ask.cards) or d.cards:  # 自由記述の質問でも、挙げたカードがあれば見せる
                line += "\n    選んだカード: %s" % (", ".join("%s <%s>" % (c, state.cards[c].name) for c in d.cards) or "なし")
        out.append(line)
    if play.game_start_pending(state):
        rows = []
        for p in state.player_order:
            n, done = play.mulligans(state, p), play.bottomed(store, p)
            rows.append("%s マリガン %d 回%s" % (p, n, "" if not n else "（下に置いた %d 枚%s）"
                                                 % (done, "。済み" if done >= n else "。残り %d 枚を本人に聞く" % (n - done))))
        out.append("ゲーム開始の処理（全員キープした）: " + "、".join(rows))
    elif play.resolve_pending(state):
        top = state.stack.items[0]
        out.append("全員がパスした。スタックの一番上（%s、コントローラー %s）を解決する。解決の後、次に優先権を持つ Player が"
                   "対応しないと分かれば、その Player の分もパスして進める（止める場所・判断の決まりに従う）" % (top.id, top.controller))
    elif play.continue_pending(state):
        d = play.continue_pending(state)
        out.append("相手は対応しなかった。%s の計画 #%d（下の「進行中の計画」）の続きを処理する: まだ書いていない行と"
                   "「その後」（%s）。済んだ行は書き直さない" % (d.player, d.seq, play._then_text(play.intent_then(d))))
    elif play.turn_start_pending(state):
        out.append("%s のターンが終わった。%s を actor にして次のターンを始める（止める理由が無ければ turn_start で to は main1）"
                   % (state.turn.active, play.next_turn_player(state)))
    elif not req["pending"]:
        out.append("（なし）")
    if not play.turn_start_pending(state):  # ターンが終わった後は、終わったターンの計画を見せない
        out += _plan_section(state, {d.seq for d in req["pending"]})
    standing = standing_requests(state, req["pending"])
    if standing:
        out += ["", "# 先の指示（このターンと前のターンの処理済みの依頼。選択を聞く前に見る）"]
        out += ["#%d T%d %s %s: %s" % (d.seq, d.turn, d.step, d.player, quote(d.text)) for d in standing]
    if req["asks"]:
        out += ["", "# 未回答の質問"] + ["#%d → %s: %s" % (d.seq, d.player, quote(d.text)) for d in req["asks"]]
    out += ["", "# 止める場所（各 Player の非公開の設定）"] + ["%s: %s" % (p, _stops_text(store, p)) for p in state.player_order]
    out += ["", "# 最近の記録（前回のあなたの処理から）"]
    out += [_entry_line(e) for e in recent_entries(store)] or ["（なし）"]
    view = info.player_view(state, None, graveyard=True)
    out += ["", "# 盤面（全情報）", render_view(view).rstrip(),
            "", "# 依頼", "上の依頼を処理し、JSON のコード・ブロック（batch・ask・message）で返す。"]
    return "\n".join(out) + "\n"


def _plan_section(state, pending=()) -> list:
    """審判に見せる、アクティブ・プレイヤーの進行中の計画（行の番号と、まだ書いていない行・割り込み）。"""
    plan = play.plan_in_progress(state)
    if plan is None or not plan.plan:
        return []
    rest = play.plan_rest(state, plan)
    out = ["", "# 進行中の計画（%s の依頼 #%d。止めるときは返答の rest に、まだ書いていない行の番号を書く）" % (plan.player, plan.seq)]
    if plan.seq in pending:
        out.append("上の「処理する依頼」#%d の行動（番号は行動の行の番号）" % plan.seq)
    else:
        out += ["行 %d: %s" % (i, quote(str(x.get("text", "")))) for i, x in enumerate(plan.plan, 1)]
        out.append("その後: %s" % play._then_text(play.intent_then(plan) or "continue"))
    out.append("まだ書いていない行: %s" % ("分からない（記録で確かめる）" if rest is None
                                          else ", ".join(map(str, rest)) or "なし（全部書いた）"))
    if play.interrupted(state, plan):
        out.append("相手が割り込んだ。この計画の残りは %s が決め直す（あなたは続けない）" % plan.player)
    return out


STANDING = 8  # 審判に見せる先の指示の上限（新しいものから）


def standing_requests(state, pending: list) -> list:
    """このターンと前のターンに Player が出した、処理済みの依頼（文のあるもの）。補足に書いた先の選択
    （「エンド時の誘発は引く」）を、依頼を処理した後の審判も読めるように。"""
    skip = {d.seq for d in pending}
    out = [d for d in state.declarations if d.kind == "intent" and d.seq not in skip and d.text
           and d.turn >= state.turn.turn - 1]
    return out[-STANDING:]


def recent_entries(store: GameStore) -> list:
    """審判に見せる最近の記録: 前回の審判の処理の印（ruled）から後。毎回送る入力を減らすため、それより前は出さない
    （前回の処理の内容は ruled の文に要約されている）。少なくとも RECENT_MIN 件、多くても RECENT 件。"""
    log = store.read_log()[:store.cursor()]
    last = next((i for i in range(len(log) - 1, -1, -1)
                 if any(op["op"] == "declare" and op.get("kind") == "ruled" for op in log[i]["act"])), 0)
    start = max(0, min(last, len(log) - RECENT_MIN), len(log) - RECENT)
    return log[start:]


def request_body(system: list, user: str, role: Optional[str] = None) -> dict:
    """API の本文（OpenAI の Chat Completions。llm.py）。固定部分を先頭の system に、今回の分を最後の user に置く。"""
    from . import llm
    return llm.request_body(system, user, role)


# ---------------------------------------------------------------- 書き出し

def prompt_dir(store: GameStore, role: str) -> pathlib.Path:
    return store.root / "prompts" / role


def memo_path(store: GameStore, seat: str) -> pathlib.Path:
    return prompt_dir(store, seat) / "memo.md"


def pending_path(store: GameStore) -> pathlib.Path:
    """最後に作ったプロンプトの控え（どの役割の返答を待っているか）。answer が使う。"""
    return store.root / "prompts" / "pending.json"


def _write_if_changed(path: pathlib.Path, text: str) -> None:
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def build(store: GameStore, role: str, error: Optional[str] = None, mode: Optional[str] = None) -> dict:
    """role（Player の席か "judge"）のプロンプトを書き出す。mode は Player なら intent（既定）か direct。

    prompts/<role>/system-1.md・system-2.md   固定部分（変わらなければ書き直さない）
    prompts/<role>/NNNN.user.md                今回のメッセージ（NNNN は cursor。同じ cursor の作り直しは -2, -3…）
    prompts/<role>/NNNN.request.json           API の本文（OpenAI の Chat Completions）
    prompts/<role>/latest.md                   手動用: system と user を1つに（そのまま貼る）
    prompts/pending.json                       返答を待っている役割と cursor（answer が使う）
    """
    mode = JUDGE if role == JUDGE else (mode or "intent")
    if mode not in MODES:
        raise ValueError("unknown mode %r" % mode)
    d = prompt_dir(store, role)
    d.mkdir(parents=True, exist_ok=True)
    if role == JUDGE:
        system, user = [role_system("judge"), game_system(store)], judge_message(store, error)
    else:
        system, user = [role_system(mode), seat_system(store, role)], user_message(store, role, error, mode)
    for i, s in enumerate(system, 1):
        _write_if_changed(d / ("system-%d.md" % i), s)
    cur = store.cursor()
    stem, n = "%04d" % cur, 1
    while (d / ("%s.user.md" % stem)).exists():
        n += 1
        stem = "%04d-%d" % (cur, n)
    (d / ("%s.user.md" % stem)).write_text(user, encoding="utf-8")
    _dump(d / ("%s.request.json" % stem), request_body(system, user, role))
    (d / "latest.md").write_text("\n\n".join(
        ["<!-- system 1/2（固定・役割） -->", system[0], "<!-- system 2/2（固定・%s） -->" % ("対局" if role == JUDGE else "この席"),
         system[1], "<!-- user（今回） -->", user]), encoding="utf-8")
    pending = {"role": role, "mode": mode, "cursor": cur, "stem": stem}
    _dump(d / "pending.json", pending)
    _dump(pending_path(store), pending)
    return {"dir": d, "role": role, "mode": mode, "stem": stem, "cursor": cur, "latest": d / "latest.md",
            "request": d / ("%s.request.json" % stem), "waiting_on": play.waiting_on(store.load())}


def next_role(store: GameStore, ai: list) -> Optional[str]:
    """今プロンプトを作るべき役割（審判か、AI の席）。人間を待っているなら None。"""
    wait = play.waiting_on(store.load())
    return wait if wait == JUDGE or wait in ai else None


def ai_seats(store: GameStore) -> list:
    """AI の席: 鍵（invite）の無い席。鍵のある席は GUI の人間。"""
    humans = play.seats(store)
    return [p for p in store.load().player_order if p not in humans]


def auto_build(store: GameStore) -> Optional[dict]:
    """審判か AI の席の番になっていて、その時点のプロンプトがまだ無ければ作る（GUI の依頼・answer の後に呼ぶ）。

    鍵を作った対局（GUI の人間がいる対局）だけ。同じ役割・同じ cursor のプロンプトがあれば作り直さない。"""
    if not play.seats(store):
        return None
    play.autopass(store)
    role = next_role(store, ai_seats(store))
    if not role:
        return None
    own = _pending(store, role)
    if own.get("cursor") == store.cursor():
        return None
    return build(store, role)


# ---------------------------------------------------------------- 返答

_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def _last_json(text: str) -> dict:
    blocks = _JSON_BLOCK.findall(text)
    raw = blocks[-1] if blocks else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("answer has no valid JSON block: %s" % e)
    if not isinstance(data, dict):
        raise ValueError("the JSON block must be an object")
    return data


def parse_answer(text: str) -> dict:
    """direct の返答: {"batch": {...} | null, "memo": "..."}。"""
    data = _last_json(text)
    if "batch" not in data:
        raise ValueError('answer must be {"batch": {...}, "memo": "..."}')
    batch = data["batch"]
    if batch is not None and (not isinstance(batch, dict) or not isinstance(batch.get("acts"), list)):
        raise ValueError("batch must be an object with acts (or null)")
    return data


def _result(ok, result=None, error=None, next_=None) -> dict:
    return {"ok": ok, "result": result, "error": error, "next": next_}


def _apply(store: GameStore, batch: dict, expect: Optional[int]):
    """Batch を適用する。返り値は (結果, 失敗の文) 。"""
    try:
        result = store.apply(batch, expect=expect)
    except StaleCursor:
        return None, "返答を待つ間に盤面が進んだ（別の誰かが書いた）。最新の盤面で書き直す。"
    result.pop("_state", None)
    stopped = result["stopped"]
    if stopped:
        return result, "Act %d が %s: %s" % (stopped["at"] + 1, stopped["reason"], stopped.get("error") or "")
    return result, None


def _save_response(store: GameStore, role: str, pending: dict, text: str) -> None:
    d = prompt_dir(store, role)
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.response.md" % pending.get("stem", "%04d" % store.cursor()))).write_text(text, encoding="utf-8")


def _pending(store: GameStore, role: str) -> dict:
    p = prompt_dir(store, role) / "pending.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _finish(store: GameStore, role: str, mode: str, result, error) -> dict:
    """適用の後: 失敗なら理由付きでプロンプトを作り直す。成功ならその役割の控えを消す（同じ返答を二度適用しない）。"""
    if error:
        return _result(False, result, error, build(store, role, error=error, mode=mode))
    (prompt_dir(store, role) / "pending.json").unlink(missing_ok=True)
    pp = pending_path(store)
    if pp.exists() and json.loads(pp.read_text(encoding="utf-8")).get("role") == role:
        pp.unlink()
    return _result(True, result)


def apply_answer(store: GameStore, seat: str, text: str) -> dict:
    """direct の返答の Batch を seat として適用し、memo と返答を保存する。まだ seat の番なら次のプロンプトも作る。"""
    pending = _pending(store, seat)
    _save_response(store, seat, pending, text)
    try:
        data = parse_answer(text)
    except ValueError as e:
        return _result(False, None, str(e), build(store, seat, error=str(e), mode="direct"))
    _write_memo(store, seat, data.get("memo"))
    batch = data["batch"]
    if batch is None:
        return _result(True)
    if play.seats(store):  # 審判のいる対局（鍵の対局）では、席は卓を動かさない（依頼だけ。request_play_design）
        err = "this game has a judge; a seat writes only requests (use the intent mode)"
        return _result(False, None, err)
    for g in batch["acts"]:
        if isinstance(g, dict) and g.get("actor", seat) != seat:
            err = "acts must not set another actor (use proxy only for no-choice progress)"
            return _result(False, None, err, build(store, seat, error=err, mode="direct"))
    result, error = _apply(store, {**batch, "actor": seat}, pending.get("cursor"))
    out = _finish(store, seat, "direct", result, error)
    if out["ok"] and play.waiting_on(store.load()) == seat:
        out["next"] = build(store, seat, mode="direct")
    return out


INTENT_WRITES = ("request", "declare", "answer")


def intent_batch(state, seat: str, data: dict) -> dict:
    """intent の返答の {"request": {...}} / {"declare": {...}} / {"answer": {...}} を、その席の Batch にする
    （席の API と同じ形。play.seat_batch が卓の宣言に変え、出せる場面かを確かめる）。"""
    given = [w for w in INTENT_WRITES if w in data]
    if len(given) != 1 or not isinstance(data[given[0]], dict):
        raise ValueError('answer must have exactly one of "request", "declare", "answer" (an object)')
    return play.seat_batch(state, seat, given[0], data[given[0]])


def apply_intent(store: GameStore, seat: str, text: str) -> dict:
    """intent の返答（request / declare / answer と memo・stops）を、その席の書き込みとして卓に書く。"""
    pending = _pending(store, seat)
    _save_response(store, seat, pending, text)
    try:
        data = _last_json(text)
        batch = intent_batch(store.load(), seat, data)
        stops = play.check_stops(data["stops"]) if data.get("stops") is not None else None
    except ValueError as e:
        return _result(False, None, str(e), build(store, seat, error=str(e), mode="intent"))
    _write_memo(store, seat, data.get("memo"))
    if stops is not None:  # 止める場所は非公開（卓の記録に載せず、席のファイルに置く）
        play.set_stops(store, seat, stops)
    result, error = _apply(store, batch, pending.get("cursor"))
    return _finish(store, seat, "intent", result, error)


def escalate(store: GameStore, seat: str, error: str, response: str) -> dict:
    """AI の席の返答が続けて卓に書けなかったとき、その席の名前で審判に回す（質問されていれば回答として、
    でなければ依頼の補足として）。審判が状況を見て、席の意図を汲んで処理するか聞き直す。"""
    state = store.load()
    head = "（自動）%s の返答が続けて卓に書けなかった。理由: %s\n最後の返答: " % (seat, error[:300])
    text = head + response.strip()[:play.MAX_TEXT - len(head)]
    asked = any(d.player == seat for d in play.requests(state)["asks"])
    try:
        batch = play.seat_batch(state, seat, *(("answer", {"text": text}) if asked else ("request", {"comment": text})))
    except (ValueError, play.Refused) as e:
        return _result(False, None, str(e))
    result, error = _apply(store, batch, store.cursor())
    return _finish(store, seat, "intent", result, error)


# 審判の1回の返答で書ける量（長い Batch は出力が切れたり途中で崩れたりする）。ループは judge.md「ループ」のとおり、
# 1回に LOOP_PER_BATCH 周まで・1つの指定で LOOP_MAX 周まで（judge.md の数字と合わせる）
JUDGE_MAX_OPS = 120  # 過去の審判の Batch で卓に書けた最大は 109 op（g1-seed7）。111・334 op のものは書けていない
LOOP_PER_BATCH = 5
LOOP_MAX = 20


def judge_acts(state, data: dict) -> list:
    """審判の返答を、卓に書く Act の並びにする: batch の各 Act（actor 付き）→ ruled → ask。"""
    req = play.requests(state)
    if req["pending"]:
        default, what = req["pending"][-1].player, " ".join("#%d" % d.seq for d in req["pending"])
    elif play.game_start_pending(state):
        default, what = state.turn.active, "ゲーム開始"  # ゲーム開始の処理は先攻の名前で印を付ける
    elif play.resolve_pending(state):
        default, what = state.stack.items[0].controller, "スタックの解決"  # 全員パスの後の解決
    elif play.continue_pending(state):
        d = play.continue_pending(state)
        default, what = d.player, play.continued_label(d.seq)  # 途中で止めた依頼の「その後」（ターン終了など）
    elif play.turn_start_pending(state):
        default, what = play.next_turn_player(state), "ターン開始"  # クリンナップの後、次のターンを始める
    else:
        raise ValueError("there is no request for the judge")
    acts = []
    batch = data.get("batch")
    if batch is not None:
        if not isinstance(batch, dict) or not isinstance(batch.get("acts"), list):
            raise ValueError("batch must be an object with acts (or null)")
        n = sum(len(g.get("act") or []) + (1 if g.get("proc") else 0) for g in batch["acts"] if isinstance(g, dict))
        if n > JUDGE_MAX_OPS:
            raise ValueError("the batch has %d ops (max %d). Write fewer at a time: for a loop, at most %d iterations "
                             "per batch, stopping after both players pass with an item on the stack, and say "
                             "\"ループ k/N 周目まで\" in message" % (n, JUDGE_MAX_OPS, LOOP_PER_BATCH))
        for g in batch["acts"]:
            if not isinstance(g, dict):
                raise ValueError("each act must be an object")
            if "proxy" in g:
                raise ValueError("the judge writes each act with its actor; do not use proxy")
            actor = g.get("actor", default)
            if actor not in state.players:
                raise ValueError("unknown actor %r" % actor)
            if g.get("proc") == "turn_start" and not g.get("to"):
                g = {**g, "to": "main1"}  # 書き忘れるとドロー・ステップで止まり、Player がメインで行動できない
            acts.append({**g, "actor": actor})
    done = what
    message = str(data.get("message") or "").strip()
    ruled = {"op": "declare", "player": default, "kind": "ruled", "text": "%s %s" % (done, message) if message else done}
    plan = play.plan_in_progress(state)
    rest = data.get("rest")
    # 進行中の計画そのものを処理した（本人の依頼・その続き・本人の呪文の解決・計画の途中で聞いた質問への本人の回答）なら、
    # その印と、どこまで書いたか。相手の依頼（割り込み）の処理では印を付けない（計画は進んでいない）
    top = state.stack.items[0] if state.stack.items else None
    last = play.plan_ruled(state, plan) if plan is not None else None
    answered = last is not None and any(d.kind == "answer" and d.player == plan.player and d.seq > last.seq
                                        for d in req["pending"])
    if plan is not None and (any(d.seq == plan.seq for d in req["pending"]) or what == play.continued_label(plan.seq)
                             or answered or (not req["pending"] and top is not None and top.controller == plan.player)):
        ruled["of"] = plan.seq
        if plan.plan and rest is not None:
            if not isinstance(rest, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in rest):
                raise ValueError("rest must be a list of the plan's line numbers not written yet (e.g. [4, 5]); [] if all written")
            ruled["rest"] = [i for i in rest if 1 <= i <= len(plan.plan)]
        elif plan.plan and not data.get("ask"):
            # rest を書かずに質問もしていない: 止めていない（全部書いた）として扱う。前の rest のままにすると、
            # 済んだ行が「残り」として本人の下書きに戻ってしまう
            ruled["rest"] = []
    acts.append({"act": [ruled], "label": "審判: %s を処理" % done})
    ask = data.get("ask")
    if ask:
        if not isinstance(ask, dict) or ask.get("to") not in state.players or not ask.get("text"):
            raise ValueError('ask must be {"to": "pN", "text": "...", "choices": [...]}')
        if ask["to"] != state.turn.active and "ブロック" in str(ask["text"]) + "".join(map(str, ask.get("choices") or [])):
            # ブロックは防御側が GUI・依頼で決める（質問の選択肢では選ばせない）
            raise ValueError("do not ask how to block: move to declare_blockers and give priority to the defending "
                             "player (op priority), then stop. The player sends blocks as a request")
        op = {"op": "declare", "player": ask["to"], "kind": "ask", "text": str(ask["text"]),
              "choices": list(ask.get("choices") or [])}
        if ask.get("cards"):  # カードを選ばせる質問（GUI では候補のカードを押して選ぶ）
            op.update({k: ask[k] for k in ("cards", "min", "max") if k in ask})
        acts.append({"act": [op], "label": "審判の質問 → %s" % ask["to"]})
    return [batch.get("label") if batch else None, acts]


def apply_judge(store: GameStore, text: str) -> dict:
    """審判の返答 {"batch", "ask", "message"} を卓に書く（Act ごとに actor。最後に ruled と質問）。"""
    pending = _pending(store, JUDGE)
    _save_response(store, JUDGE, pending, text)
    try:
        label, acts = judge_acts(store.load(), _last_json(text))
    except ValueError as e:
        return _result(False, None, str(e), build(store, JUDGE, error=str(e)))
    label = (label or "処理").strip()
    batch = {"actor": None, "label": label if label.startswith("審判") else "審判: " + label, "acts": acts}
    result, error = _apply(store, batch, pending.get("cursor"))
    return _finish(store, JUDGE, JUDGE, result, error)


def apply_response(store: GameStore, text: str, role: Optional[str] = None) -> dict:
    """最後に作ったプロンプト（pending.json）の役割として返答を適用する。role で指定もできる。"""
    pp = pending_path(store)
    pending = json.loads(pp.read_text(encoding="utf-8")) if pp.exists() else {}
    role = role or pending.get("role")
    if not role:  # 役割ごとの控えしか無い（前の版で作ったプロンプト）なら、それが1つのときだけ使う
        roles = [d.name for d in (store.root / "prompts").glob("*") if (d / "pending.json").exists()]
        if len(roles) != 1:
            raise ValueError("no pending prompt (run next / prompt first)")
        role = roles[0]
    own = _pending(store, role)
    # mode の無い控えは、審判を入れる前の版（Player が Batch を直接書く形）で作ったもの
    mode = own.get("mode") or ("judge" if role == JUDGE else "direct" if own else "intent")
    if role == JUDGE:
        out = apply_judge(store, text)
    elif mode == "direct":
        out = apply_answer(store, role, text)
    else:
        out = apply_intent(store, role, text)
    out["role"] = role
    return out
