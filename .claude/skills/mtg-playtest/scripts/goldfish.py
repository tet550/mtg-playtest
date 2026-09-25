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
    fields = {"deck": st["players"][SEAT].get("deck"),
              "on": "play" if st.get("first") == SEAT else "draw",
              "lethal_turn": own_turn(st) if winner == SEAT else None}
    if st["players"][SEAT].get("swaps"):
        fields["swap"] = st["players"][SEAT]["swaps"]
    return fields


def is_record(rec):
    return bool(rec.get("goldfish"))


def group_key(rec):
    g = rec["goldfish"]
    swap = " [%s]" % " / ".join(g["swap"]) if g.get("swap") else ""
    return rec.get("tag") or "goldfish: %s%s" % (g.get("deck") or "P1", swap)


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


# ---------------------------------------------------------------- カード入れ替えの比較

def library_order(names, seed, pid=SEAT):
    """init と同じシャッフル。枚数とseedだけで並べ替えが決まるので、同じ位置の
    名前を差し替えたデッキでも他のカードは同じ順に並ぶ（入れ替えの対照比較の前提）。"""
    import random
    perm = list(range(len(names)))
    random.Random("%s:deck:%s" % (seed, pid)).shuffle(perm)
    return perm


def parse_swap(spec):
    old, sep, new = spec.partition("=")
    if not sep or not old.strip() or not new.strip():
        raise SystemExit("--swap は \"旧カード=新カード\" の形で指定してください: %s" % spec)
    return old.strip(), new.strip()


def apply_swaps(names, swaps, resolve):
    """swaps=[(旧, 新)] を、シャッフル前の並びで旧カードの先頭の未交換枠から1枚ずつ置き換える。

    resolve は名前を英語の正規名にする関数。戻り値は (新しい並び, 置き換えた枠, 表示用の記録)。
    """
    names, slots, labels = list(names), [], []
    for old, new in swaps:
        old_name, new_name = resolve(old), resolve(new)
        key = old_name.casefold()
        hit = next((i for i, n in enumerate(names) if n.casefold() == key and i not in slots), None)
        if hit is None:
            raise SystemExit("入れ替え元「%s」がデッキに（残って）いません。" % old)
        names[hit] = new_name
        slots.append(hit)
        labels.append("%s→%s" % (old_name, new_name))
    return names, slots, labels


def seed_hits(names, slots, seed, within, pid=SEAT):
    """その seed で、指定の枠のうち山札の上 within 枚に入るものの位置（1始まり）。"""
    perm = library_order(names, seed, pid)
    return sorted(pos + 1 for pos, i in enumerate(perm[:within]) if i in slots)


def pair_key(rec):
    return (rec.get("seed"), rec["goldfish"].get("on"))


def render_pair(base_tag, base, alt_tag, alt):
    """同じ seed・同じ先後の記録どうしを並べ、自ターンの差を集計する。"""
    left = {pair_key(r): r for r in base}
    right = {pair_key(r): r for r in alt}
    keys = sorted(set(left) & set(right), key=lambda k: (str(k[1]), k[0] if k[0] is not None else -1))
    lines = ["=== 対照比較: %s（基準） vs %s ===  %d組" % (base_tag, alt_tag, len(keys))]
    if not keys:
        lines.append("  同じ seed・先後の組がありません。")
        return lines
    swap = next((r["goldfish"].get("swap") for r in alt if r["goldfish"].get("swap")), None)
    if swap:
        lines.append("  入れ替え: " + " / ".join(swap))
    diffs, faster, same, slower, missing = [], 0, 0, 0, 0
    rows = [["seed", "先後", base_tag, alt_tag, "差"]]
    for k in keys:
        a, b = left[k]["goldfish"]["lethal_turn"], right[k]["goldfish"]["lethal_turn"]
        if a is None or b is None:
            missing += 1
            d = "-"
        else:
            diffs.append(b - a)
            faster += b < a
            same += b == a
            slower += b > a
            d = "%+d" % (b - a)
        rows.append([str(k[0]), "先手" if k[1] == "play" else "後手",
                     str(a) if a is not None else "なし", str(b) if b is not None else "なし", d])
    width = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines.extend("  " + "  ".join(r[i].ljust(width[i]) for i in range(len(r))).rstrip() for r in rows)
    if diffs:
        lines.append("  平均差 %+.2f（負なら比較側が速い） / 速い %d・同じ %d・遅い %d"
                     % (sum(diffs) / len(diffs), faster, same, slower))
    if missing:
        lines.append("  リーサルなしを含む組 %d（平均差から除外）" % missing)
    unpaired = len(set(left) ^ set(right))
    if unpaired:
        lines.append("  相手のいない記録 %d件（seed・先後が一致しないもの）" % unpaired)
    return lines
