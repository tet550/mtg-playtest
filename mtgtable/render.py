"""Player View のテキスト表示。AI と人間の両方が読む前提で、1行1オブジェクトにまとめる。

ゲームモデルと表示を分ける（24〜25節）ため、ここは view(dict) だけを入力にする。
表示上の整理だけを行う:
- 完全に同じ状態のオブジェクト（同じトークン10体など）は1行にまとめる
- 同じ内容の Note は ×N でまとめる
- 連番の id は #t5..#t14 のように縮める
"""
from __future__ import annotations

import json
import re

_ID = re.compile(r"^#([a-z]+)(\d+)$")


def card_name(name: str) -> str:
    """表示でのカード名の書き方: <Hired Claw>（地の文・id・Note と区別するため。JSON の値には付けない）。"""
    return "<%s>" % name


def id_ranges(ids: list) -> str:
    """["#t5", "#t6", "#t7", "#c2"] -> "#t5..#t7 #c2"。"""
    out, run = [], []

    def flush():
        if not run:
            return
        out.append(run[0][2] if len(run) == 1 else
                   ("%s %s" % (run[0][2], run[1][2]) if len(run) == 2 else "%s..%s" % (run[0][2], run[-1][2])))
        run.clear()

    for i in ids:
        m = _ID.match(i)
        if m and run and run[-1][0] == m.group(1) and run[-1][1] + 1 == int(m.group(2)):
            run.append((m.group(1), int(m.group(2)), i))
            continue
        flush()
        if m:
            run.append((m.group(1), int(m.group(2)), i))
        else:
            out.append(i)
    flush()
    return " ".join(out)


def _notes(notes: list) -> str:
    groups = {}
    for n in notes:
        groups.setdefault((n["text"], n.get("until")), []).append(n["id"])
    parts = []
    for (text, until), ids in groups.items():
        body = text + (" until " + until if until else "")
        if len(ids) > 1:
            parts.append("[%s ×%d: %s]" % (body, len(ids), id_ranges(ids)))
        else:
            parts.append("[%s %s]" % (ids[0], body))
    return "  " + "  ".join(parts) if parts else ""


def _definition(d: dict) -> str:
    if not d:
        return ""
    items = []
    if d.get("copy_of"):
        items.append("copy of %s" % d["copy_of"])
    items += ["%s=%s" % (k, v) for k, v in d.items() if k != "copy_of"]
    return "  {%s}" % ", ".join(items)


def _card(cv: dict, ids: str = None, count: int = 1) -> str:
    ident = ids or cv["id"]
    if cv.get("hidden"):
        s = "%s[face-down]" % ident if cv.get("face_down") else "%s[hidden]" % ident
    else:
        s = ident + (" " + card_name(cv["name"]) if cv.get("name") else "")
    if count > 1:
        s += " ×%d" % count
    flags = []
    if cv.get("token"):
        flags.append("token")
    if cv.get("tapped"):
        flags.append("tapped")
    if cv.get("new"):
        flags.append("new")
    if cv.get("face_down") and not cv.get("hidden"):
        flags.append("face-down")
    if cv.get("face"):
        flags.append("face=%s" % cv["face"])
    if cv.get("controller") and cv["controller"] != cv.get("owner"):
        flags.append("ctrl=%s owner=%s" % (cv["controller"], cv["owner"]))
    if cv.get("counters"):
        flags.append(" ".join("%s:%d" % kv for kv in cv["counters"].items()))
    if cv.get("index") is not None:
        flags.append("index %d" % cv["index"])
    if flags:
        s += " (" + ", ".join(flags) + ")"
    if cv.get("links"):
        s += "  [" + "; ".join(cv["links"]) + "]"
    s += _notes(cv.get("notes", []))
    s += _definition(cv.get("definition"))
    return s


def _grouped(cards: list) -> list:
    """同じ状態のオブジェクトを1行に。Note は id が違っても内容が同じなら同じとみなす。"""
    groups = {}
    for c in cards:
        if "name" not in c and not c.get("definition"):
            # 名前を伏せた表示では中身が同じか分からないので、まとめない
            groups[c["id"]] = [c]
            continue
        key = {k: v for k, v in c.items() if k not in ("id", "notes")}
        key["notes"] = sorted((n["text"], n.get("until") or "") for n in c.get("notes", []))
        groups.setdefault(json.dumps(key, sort_keys=True, ensure_ascii=False), []).append(c)
    lines = []
    for members in groups.values():
        if len(members) == 1:
            lines.append(_card(members[0]))
        else:
            notes = [n for m in members for n in m.get("notes", [])]
            head = dict(members[0], notes=notes)
            lines.append(_card(head, id_ranges([m["id"] for m in members]), len(members)))
    return lines


def _cards_block(title: str, zv: dict, indent: str = "  ") -> list:
    lines = []
    count = zv["count"]
    if "cards" in zv:
        lines.append("%s%s (%d)%s" % (indent, title, count, "" if zv["cards"] else ": -"))
        lines.extend(indent + "  " + x for x in _grouped(zv["cards"]))
        return lines
    known = zv.get("known", [])
    positions = zv.get("known_positions", [])
    unordered = zv.get("known_unordered", [])
    head = "%s%s (%d)" % (indent, title, count)
    if not known and not positions and not unordered:
        return [head + ": hidden"]
    lines.append(head + ":")
    lines.extend(indent + "  " + x for x in _grouped(known))
    lines.extend(indent + "  " + _card(c) for c in positions)
    if unordered:
        lines.append(indent + "  known, position unknown:")
        lines.extend(indent + "    " + x for x in _grouped(unordered))
    rest = count - len(known) - len(positions) - len(unordered)
    if rest:
        lines.append(indent + "  + %d unknown" % rest)
    return lines


def render_view(view: dict) -> str:
    t = view["turn"]
    out = ["== view as %s (policy %s) v%d ==" % (view["viewer"] or "judge", view["policy"], view["version"])]
    if (t["phase"], t["step"]) == ("pregame", "pregame"):
        line = "Pregame  first %s  priority %s" % (t["active"], t["priority"] or "-")
    else:
        line = "Turn %d  active %s  %s/%s  priority %s" % (t["turn"], t["active"], t["phase"], t["step"],
                                                         t["priority"] or "-")
    if t.get("passed"):
        line += "  passed: " + ", ".join(t["passed"])
    if t.get("standing_passes"):
        line += "  standing pass: " + ", ".join("%s until %s" % kv for kv in t["standing_passes"].items())
    out.append(line)
    zones = view["zones"]
    for p in view["players"]:
        pid = p["id"]
        head = "-- %s %s  life %d" % (pid, p["name"], p["life"])
        if p["status"] != "playing":
            head += "  [%s]" % p["status"]
        if p.get("counters"):
            head += "  " + " ".join("%s:%d" % kv for kv in p["counters"].items())
        out.append(head + _notes(p.get("notes", [])))
        if p["mana"]:
            parts = []
            for m in p["mana"]:
                s = "%s %d%s" % (m["id"], m["amount"], m["color"])
                extra = [x for x in (m.get("source") and "from " + m["source"], m.get("duration")) if x]
                extra += [n["text"] for n in m.get("notes", [])]
                if extra:
                    s += "(" + "; ".join(extra) + ")"
                parts.append(s)
            out.append("  mana: " + ", ".join(parts))
        for kind in ("hand", "library", "graveyard", "sideboard"):
            zv = zones.get("%s.%s" % (pid, kind))
            if zv is None:
                continue
            if zv.get("collapsed") or (kind == "library" and not any(
                    k in zv for k in ("known_positions", "known_unordered", "cards"))):
                extra = ", known positions: %d" % zv["known_positions_count"] if zv.get("known_positions_count") else ""
                out.append("  %s (%d%s)" % (kind, zv["count"], extra))
                continue
            out.extend(_cards_block(kind, zv))
        bf = [c for c in zones["battlefield"]["cards"] if c.get("controller", c["owner"]) == pid]
        bf = [c for c in bf if c.get("land")] + [c for c in bf if not c.get("land")]  # 土地を前に
        out.append("  battlefield (%d)%s" % (len(bf), "" if bf else ": -"))
        out.extend("    " + x for x in _grouped(bf))
    for z in ("exile", "command"):
        zv = zones[z]
        if zv["count"]:
            out.append("-- " + z)
            out.extend("  " + x for x in _grouped(zv["cards"]))
    if view["stack"]:
        out.append("-- stack (top first)")
        by_id = {c["id"]: c for c in zones["stack"]["cards"]}
        for it in view["stack"]:
            s = "  %s %s by %s" % (it["id"], it["kind"], it["controller"])
            if it.get("card"):
                s += ": " + (_card(by_id[it["card"]]) if it["card"] in by_id else it["card"])
            if it.get("source"):
                s += " source=" + it["source"]
            if it.get("text"):
                s += "  \"%s\"" % it["text"]
            out.append(s)
    cb = view["combat"]
    if cb["attacks"]:
        out.append("-- combat")
        for a in cb["attacks"]:
            blockers = [b["blocker"] for b in cb["blocks"] if b["attacker"] == a["attacker"]]
            out.append("  %s -> %s%s" % (a["attacker"], a["target"],
                                         "  blocked by " + ", ".join(blockers) if blockers else ""))
    # カードの行に出したもの（source がカードの Link）は繰り返さない
    shown = {c["id"] for zv in zones.values() for key in ("cards", "known", "known_positions", "known_unordered")
             for c in zv.get(key, []) if c.get("links")}
    rest = [l for l in view["links"] if l["source"] not in shown]
    if rest:
        out.append("-- links")
        for l in rest:
            out.append("  %s %s: %s -> %s%s" % (l["id"], l["kind"], l["source"], ", ".join(l["targets"]),
                                                "  \"%s\"" % l["text"] if l.get("text") else ""))
    if view["declarations"]:
        out.append("-- declarations this step: " +
                   ", ".join("%s %s" % (d["player"], d["kind"]) for d in view["declarations"]))
    return "\n".join(out)


_NAMED_ID = re.compile(r"(#[a-z]+\d+)(?:\([^()]*\)| <[^<>]*>)")


def strip_names(text: str) -> str:
    """ログのイベント "#c33 <Mona Lisa, Science Geek>"（古いログは "#c33(...)"）からカード名を落とす。"""
    return _NAMED_ID.sub(r"\1", text)
