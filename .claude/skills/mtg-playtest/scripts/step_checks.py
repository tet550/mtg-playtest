"""Timing candidates, not trigger adjudication. Reuse the pending ledger."""
import re

TIMINGS = {
    "beginning.upkeep": r"upkeep|アップキープ",
    "beginning.draw": r"draw step|ドロー・?ステップ",
    "precombat_main": r"precombat main phase|first main phase|戦闘前メイン|第[１1一]メイン",
    "postcombat_main": r"postcombat main phase|second main phase|戦闘後メイン|第[２2二]メイン",
    "combat.begin": r"combat|戦闘",
    "combat.end": r"combat|戦闘",
    "ending.end": r"end step|終了ステップ",
}


def candidates(api, st, phase):
    for pid in ("P1", "P2"):
        for oid in st["zones"].get(pid + ":battlefield", []):
            o = api.obj(st, oid)
            card = api.card_of(st, o)
            if (phase == "precombat_main" and pid == st["active"]
                    and "saga" in [s.lower() for s in card.get("subtypes", [])]):
                yield o, "英雄譚：伝承カウンター追加・章能力を確認", True
            for line in (card.get("oracle") or "").splitlines():
                # Ignore quoted grants, reminder text, and delayed-effect instructions.
                line = re.sub(r"\([^)]*\)|（[^）]*）", "", line).strip()
                line = re.sub(r"^.*?[—–]\s*(?=At the beginning)", "", line, flags=re.I)
                condition = re.split(r"[,、]", line, maxsplit=1)[0]
                if any(x in condition for x in ('"', '“', '「')):
                    continue
                if re.search(r"\bnext\b|次の", condition, re.I):
                    continue
                if phase == "combat.end":
                    match = re.match(r"At (?:the )?end of combat\b|戦闘終了時[に、]", line, re.I)
                else:
                    match = (re.match(r"At the beginning of\b|[^。]{0,60}開始時", line, re.I)
                             and re.search(TIMINGS.get(phase, r"(?!)"), condition, re.I))
                    if phase == "combat.begin" and re.search(r"main|メイン", condition, re.I):
                        match = False
                if not match:
                    continue
                opposing = re.search(r"opponent|対戦相手", condition, re.I)
                if opposing and pid == st["active"]:
                    continue
                if not opposing and re.search(r"\byour\b|あなたの", condition, re.I) and pid != st["active"]:
                    continue
                yield o, line, False


def enter(api, st, draw_done=False):
    key = [st["turn"], st["phase"]]
    if key in st.get("checked_steps", []):
        return
    st["checked_steps"] = [k for k in st.get("checked_steps", []) if k[0] == st["turn"]] + [key]
    for o, text, action in candidates(api, st, st["phase"]):
        identity = "T%d" % st.get("next_pending", 1)
        st["next_pending"] = st.get("next_pending", 1) + 1
        st.setdefault("pending", []).append(dict(
            id=identity, text=text, controller=o["controller"],
            source=api.relations.ref(st, o["oid"]), status="review", stack_oid=None,
            parts={}, turn_action=action))
        print("!! %s 要確認 [%s] %s — %s（pending confirm / cancel --reason）" %
              (identity, o["oid"], api.disp_of(st, o), text))
    for p in st.get("pending", []):
        if (p["status"] == "scheduled" and p["at"] == st["phase"]
                and st["turn"] >= p["due_turn"]
                and (not p.get("player") or p["player"] == st["active"])):
            p["status"] = "review"
            print("!! %s 予約の要確認: %s（pending confirm / cancel --reason）" % (p["id"], p["text"]))
    if st["phase"] == "beginning.draw" and st["turn"] > 1 and not draw_done and waiting(st):
        st["timing_draw_due"] = True
        print("!! 通常ドローが先です。draw %s 1。置換・省略を処理済みならnote後にdraw %s 0。" % (st["active"], st["active"]))


def waiting(st):
    return any(p["status"] == "review" for p in st.get("pending", []))


def ahead(api, st):
    for phase in api.PHASES[api.PHASES.index(st["phase"]) + 1:]:
        if list(candidates(api, st, phase)) or any(
                p["status"] == "scheduled" and p["at"] == phase and p["due_turn"] <= st["turn"]
                and (not p.get("player") or p["player"] == st["active"])
                for p in st.get("pending", [])):
            return phase
