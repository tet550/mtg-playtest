"""Combat bookkeeping: plan without mutation, then apply resolved damage together.

Protection conditions are adjudicated by the caller, not parsed from card prose.
This is not a general replacement-effect or combat-legality engine.
"""


def plan(api, st, rows, step, trample_enabled):
    packets = []
    blocked = set(st['combat']['blocks'].values())

    def power(oid):
        value = api.pt(st, api.obj(st, oid))
        if value is None:
            raise SystemExit('P/T未登録: [%s]。card setで登録してください。' % oid)
        return max(0, value[0]) if api.deals_in(st, oid, step) else 0

    def add(source, target, amount):
        if amount > 0:
            packets.append((source, target, amount))

    for attacker, target, blockers in rows:
        remaining = power(attacker)
        trample = api.has_kw(st, attacker, 'trample', 'トランプル')
        touch = api.has_kw(st, attacker, 'deathtouch', '接死')
        if not blockers:
            if attacker not in blocked or trample:
                add(attacker, target, remaining)
            continue
        for index, blocker in enumerate(blockers):
            back = power(blocker)  # Validate every participant before applying anything.
            victim = api.obj(st, blocker)
            need = max(0, api.pt(st, victim)[1] - victim['damage'])
            if touch:
                need = min(need, 1)  # CR 702.2c: prevention does not affect assignment.
            # A legal default distribution; without trample put all leftovers on the last blocker.
            assigned = min(remaining, need) if trample or index < len(blockers) - 1 else remaining
            add(attacker, str(blocker), assigned)
            remaining -= assigned
            add(blocker, str(attacker), back)
        if trample and remaining:
            if not trample_enabled:
                raise SystemExit('トランプル超過%d点→%s。未適用です。--trampleを付けて再実行してください。'
                                 % (remaining, target))
            add(attacker, target, remaining)
    return packets


def resolve(api, st, packets, prevented=(), unprevented=()):
    """Validate explicit source:recipient decisions and resolve prevention, without mutation."""
    def recipient(value):
        return value if value in st['players'] else str(api.resolve_ref(st, value))

    def decisions(values):
        result = set()
        for value in values or ():
            parts = value.split(':')
            if len(parts) != 2:
                raise SystemExit('軽減指定は <発生源oid>:<受け手oidまたはP1/P2>。')
            result.add((api.resolve_ref(st, parts[0]), recipient(parts[1])))
        return result

    packets = [(src, recipient(dst), n) for src, dst, n in packets]
    prevent, allow = decisions(prevented), decisions(unprevented)
    pairs = {(src, dst) for src, dst, _ in packets}
    if prevent & allow or (prevent | allow) - pairs:
        raise SystemExit('軽減指定が矛盾しているか、このステップの割り振りにない組です。未適用です。')
    resolved = []
    for source, target, assigned in packets:
        pair = (source, target)
        if target not in st['players']:
            oid = int(target)
            if api.zone_of(st, oid) not in ('P1:battlefield', 'P2:battlefield'):
                raise SystemExit('ダメージの受け手[%s]が戦場にいません。未適用です。' % target)
            if api.has_kw(st, oid, 'protection', 'プロテクション') and pair not in prevent | allow:
                raise SystemExit('プロテクションを確認: --prevent %s:%s（全軽減）または '
                                 '--unprevented %s:%s（軽減なし）を指定。未適用です。'
                                 % (source, target, source, target))
        resolved.append((source, target, assigned, 0 if pair in prevent else assigned,
                         api.has_kw(st, source, 'deathtouch', '接死')))
    return resolved


def apply(api, st, resolved):
    """Apply the whole step before SBA. Record only deathtouch damage actually dealt."""
    lines = []
    for source, target, assigned, dealt, touch in resolved:
        if target in st['players']:
            st['players'][target]['life'] -= dealt
            name = target
        else:
            victim = api.obj(st, int(target))
            types = [t.lower() for t in api.card_of(st, victim).get('types', [])]
            if 'planeswalker' in types:
                victim['counters']['loyalty'] = victim['counters'].get('loyalty', 0) - dealt
            elif 'battle' in types:
                victim['counters']['defense'] = victim['counters'].get('defense', 0) - dealt
            if 'creature' in types or not {'planeswalker', 'battle'} & set(types):
                victim['damage'] += dealt
            if dealt > 0 and touch and 'creature' in types:
                victim['deathtouch_damage'] = True
            name = '%s(%s)' % (victim['name'], target)
        lines.append('%s(%s) → %s に%d点（割り振り%d・軽減%d）'
                     % (api.obj(st, source)['name'], source, name, dealt, assigned, assigned - dealt))
    return lines
