"""Presentation only: omit known boilerplate, retain unknown output verbatim."""
import difflib
import re


_BOILERPLATE = re.compile(
    r"^(?:ok|記録しました。|stack: .*|P[12] がパス → P[12] に優先権。|"
    r"両者パス → スタックの一番上を解決します。`stack resolve`|"
    r"能力 .+\(\d+\) は消滅しました。|"
    r"\s+.+\(\d+\) マナ能力: .+|"
    r"→ 効果を適用したら .*|"
    r"機械的に判定できる範囲では該当なし（誘発や継続的効果は別途確認）。)$")


def compact(text, command):
    # Inspection commands must remain complete, including stack show.
    if command[0] not in {"tap", "untap", "stack", "pass", "sba", "note"}:
        return text
    if command[:2] == ["stack", "show"]:
        return text
    return "".join(line for line in text.splitlines(keepends=True)
                   if not _BOILERPLATE.fullmatch(line.rstrip("\r\n")))


def delta(before, after):
    """Only compare authorized rendered views; never serialize hidden state."""
    if before == after:
        return "盤面差分: 変更なし\n"
    old, new = before.splitlines(), after.splitlines()
    result = []
    for tag, i, j, k, l in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # Only the uniquely labelled player summary has a stable field schema.
        # Unknown lines, objects, and hidden-view differences stay lossless.
        if tag == "replace" and j-i == l-k == 1:
            a, b = old[i].split(" | "), new[k].split(" | ")
            pattern = r"^([ ★]*P[12] .*?: )ライフ (.+)$"
            ma, mb = re.match(pattern, a[0]), re.match(pattern, b[0])
            if ma and mb and ma[1] == mb[1] and len(a) == len(b):
                aa, bb = ["ライフ " + ma[2]] + a[1:], ["ライフ " + mb[2]] + b[1:]
                fields = []
                valid = True
                for x, y in zip(aa, bb):
                    if x == y:
                        continue
                    left, sep, xv = x.partition(" ")
                    right, sep2, yv = y.partition(" ")
                    if not sep or not sep2 or left != right:
                        valid = False
                        break
                    fields.append(f"{left} {xv}→{yv}")
                if valid and fields:
                    result.append(ma[1] + " | ".join(fields))
                    continue
        result.extend("-" + x for x in old[i:j])
        result.extend("+" + x for x in new[k:l])
    return "盤面差分（- 変更前 / + 変更後 / → 値変更）\n" + "\n".join(result) + "\n"
