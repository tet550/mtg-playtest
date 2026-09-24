"""Goldfish (solo) runs: P2 never acts; measure the turn P1 first reaches lethal.

Bookkeeping only. The opponent's turn is skipped only when nothing recorded
could happen during it; otherwise the turn stops for the caller to walk through.
"""

SEAT, DUMMY = "P1", "P2"
REVEAL = 5   # show に出す山札上の既定枚数。1ターン分の計画に足り、先の読み過ぎを誘わない量（init --reveal で変更）
AFTER_LETHAL = {"end", "note", "show", "stats", "log", "sba", "zone", "hand", "look"}


def active(st):
    return bool(st and st.get("goldfish"))


def own_turn(st, turn=None):
    """P1 自身の何ターン目か（ゲーム全体のターン番号ではない）。"""
    t = st["turn"] if turn is None else turn
    return (t + 1) // 2 if st.get("first") == SEAT else t // 2


def lethal(st):
    p = st["players"][DUMMY]
    return p["life"] <= 0 or p["poison"] >= 10


def reveal_line(api, st, n=None):
    n = st.get("reveal", REVEAL) if n is None else n
    lib = st["zones"]["%s:library" % SEAT]
    cards = " ".join("%d.[%d]%s" % (i, oid, api.disp_oid(st, oid))
                     for i, oid in enumerate(lib[:n], 1))
    return "山札上（公開・先読みで判断しない）: %s%s" % (cards or "（なし）",
                                                  " …残り%d枚" % (len(lib) - n) if len(lib) > n else "")


def header(st):
    side = "先手" if st.get("first") == SEAT else "後手"
    if own_turn(st) == 0:
        return "（自ターン前・%s）" % side   # 後手のT1は相手の空ターン
    return "（自ターン%d・%s）" % (own_turn(st), side)


def opponent_turn_blockers(api, st):
    """相手ターンに起こりうる記帳済み事項。空なら相手ターンを省略してよい。"""
    reasons = []
    saved = st["active"]
    st["active"] = DUMMY
    try:
        for phase in api.PHASES:
            for o, text, _ in api.step_checks.candidates(api, st, phase):
                reasons.append("[%d]%s: %s" % (o["oid"], api.disp_of(st, o), text))
    finally:
        st["active"] = saved
    for p in st.get("pending", []):
        if p["status"] == "scheduled" and p.get("player") in (None, DUMMY):
            reasons.append("%s 予約: %s" % (p["id"], p["text"]))
    for link in st.get("links", []):
        if link.get("return") == "next-end" and link.get("status") == "waiting":
            reasons.append("%s 次の終了ステップの帰還" % link["id"])
    reasons.extend(api.bookkeeping.reminder_messages(st, "turn", DUMMY))
    return reasons


def stop_reason(before, after):
    """run の各行の後で続行してよいかを返す。止めるならその理由。"""
    if not active(after):
        return None
    if after.get("rng_seq", 0) != before.get("rng_seq", 0):
        return "乱数（シャッフル・コイン等）で結果が変わったためバッチ停止。後続行は未実行です。"
    if after["active"] == DUMMY and before["active"] != DUMMY:
        return "相手ターンに確認事項があり省略できないためバッチ停止。後続行は未実行です。"
    return None


def result_fields(st, winner):
    return {"deck": st["players"][SEAT].get("deck"),
            "on": "play" if st.get("first") == SEAT else "draw",
            "lethal_turn": own_turn(st) if winner == SEAT else None}


def is_record(rec):
    return bool(rec.get("goldfish"))


def group_key(rec):
    return rec.get("tag") or "goldfish: %s" % (rec["goldfish"].get("deck") or "P1")


def render_group(key, recs):
    lines = ["=== %s ===  %dゲーム（ゴールドフィッシュ）" % (key, len(recs))]
    for label, subset in (("全体", recs),
                          ("先手", [r for r in recs if r["goldfish"]["on"] == "play"]),
                          ("後手", [r for r in recs if r["goldfish"]["on"] == "draw"])):
        if not subset or (label != "全体" and len(subset) == len(recs)):
            continue
        turns = sorted(r["goldfish"]["lethal_turn"] for r in subset
                       if r["goldfish"]["lethal_turn"] is not None)
        misses = len(subset) - len(turns)
        line = "  %s %d件:" % (label, len(subset))
        if turns:
            mid = len(turns) // 2
            median = turns[mid] if len(turns) % 2 else (turns[mid - 1] + turns[mid]) / 2
            line += " 平均 %.2f / 中央値 %s / 最速 %d / 最遅 %d" % (
                sum(turns) / len(turns), ("%g" % median), turns[0], turns[-1])
        if misses:
            line += " / リーサルなし %d件" % misses
        lines.append(line)
        if turns:
            cells, total = [], 0
            for t in range(turns[0], turns[-1] + 1):
                total += turns.count(t)
                cells.append("T%d:%d件(累計%.0f%%)" % (t, turns.count(t), 100.0 * total / len(subset)))
            lines.append("    " + " ".join(cells))
    muls = [(r.get("mulligans") or {}).get(SEAT, 0) for r in recs]
    lines.append("  マリガン平均 %.2f" % (sum(muls) / len(muls)))
    return lines


def answers_review(rest):
    """タイミング候補で止まった直後の行が確認・取消なら、書いた本人が既に判断している。

    未確認の候補が残ったまま進む操作は bookkeeping の既存ガードが拒否する。
    """
    return bool(rest) and rest[0][2][:1] == ["pending"] and rest[0][2][1:2] in (["confirm"], ["cancel"])
