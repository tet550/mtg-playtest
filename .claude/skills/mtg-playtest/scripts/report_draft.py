"""Generate a reviewable report draft from explicit event evidence and a result.

No hidden-zone output, no inferred rulings, and no overwriting prior reports.
"""
import argparse
import json
import pathlib
import re


def draft(directory, game, seed):
    directory = pathlib.Path(directory)
    if not re.fullmatch(r'g\d+', game):
        raise ValueError('gameはg01等で指定してください')
    records = [json.loads(line) for line in (directory / 'results.jsonl').read_text(encoding='utf-8').splitlines()
               if line.strip()]
    matches = [r for r in records if r.get('seed') == seed]
    if len(matches) != 1:
        raise ValueError('seedに対応する結果が一意でありません')
    result = matches[0]
    state = json.loads((directory / (game + '.json')).read_text(encoding='utf-8'))
    if (state.get('seed') != seed or state.get('result', {}).get('winner') != result['winner']
            or state.get('turn') != result['turn']
            or {pid: state['players'][pid]['life'] for pid in ('P1', 'P2')} != result['life']):
        raise ValueError('現在の状態と結果が一致しません。undo後などは手動で確認してください')
    events, references, identities = [], [], set()
    for path in sorted((directory / 'output' / game).glob('run-*.jsonl'), key=lambda p: (p.stat().st_mtime_ns, p.name)):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        if any(str(r.get('command', '')).split()[:1] in (['undo'], ['init']) for r in rows):
            raise ValueError('undo/initを含む証跡は手動で訂正範囲を確認してください')
        ref = None
        for n, row in enumerate(rows, 1):
            event = row.get('event')
            if not event or event.get('seed') != seed:
                continue
            if event['id'] in identities:
                raise ValueError('イベントIDが重複しています: ' + event['id'])
            expected = f"T{event['turn']} {event['active']} {event['phase']}: [{event['id']}] {event['text']}"
            if expected not in state['log']:
                raise ValueError('現在のログにないイベントです。undo後などは手動で確認してください')
            start, end = event['start'], event['end']
            if not 1 <= start <= end == n or any('error' in r for r in rows[start-1:end]):
                raise ValueError('イベント参照範囲に失敗または不整合があります')
            identities.add(event['id'])
            if ref is None:
                ref = 'R%d' % (len(references) + 1)
                references.append(f'{ref} = {path.relative_to(directory).as_posix()}')
            text = ' '.join(event['text'].splitlines())
            events.append(f"- {event['id']} T{event['turn']} {event['active']} {event['phase']}: {text}。［{ref}:{start}-{end}］")
    if not events:
        raise ValueError('note --eventで記録したイベントがありません（既存noteからは推定しません）')
    first, turn = result['first'], result['turn']
    active = state['active']
    order = '先手' if active == first else '後手'
    winner = '引分' if result.get('winner') == 'draw' else result['winner']
    ending = '投了' if result.get('concede') else '通常決着'
    return '\n'.join([
        '# 対局レポート草稿', '',
        '方式: 要記入（自動生成では対局方式・ルール適法性を判定しません）。',
        f"デッキ: P1={result.get('decks', {}).get('P1')} / P2={result.get('decks', {}).get('P2')}。結果ファイル: results.jsonl", '',
        f'### {game.upper()}',
        f"条件: seed={seed} | 先手={first} | キープ=要確認（マリガン={result['mulligans']}） | state={game}.json", '',
        *events, '',
        f"結果: {winner} | {ending} | T{turn} {active}（{order}{(turn+1)//2}ターン目） | ライフ=P1 {result['life']['P1']}/P2 {result['life']['P2']}",
        '所見: 要記入。',
        '検証: 草稿・ルール確認待ち。各イベントの参照は同一バッチ内の直前イベント後から当該noteまで。', '',
        '参照:', *references, '',
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=pathlib.Path)
    parser.add_argument('--game', default='g01')
    parser.add_argument('--seed', required=True, type=int)
    parser.add_argument('--out', type=pathlib.Path, help='省略時は対局フォルダのreport-draft.md。既存ファイルは上書きしない')
    args = parser.parse_args()
    output = args.out or args.directory / 'report-draft.md'
    # References are relative to the report's game directory, not arbitrary cwd.
    if output.resolve().parent != args.directory.resolve():
        parser.error('--outは対局フォルダ直下に指定してください')
    try:
        text = draft(args.directory, args.game, args.seed)
        with output.open('x', encoding='utf-8') as stream:
            stream.write(text)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print('草稿: ' + str(output))


if __name__ == '__main__':
    main()
