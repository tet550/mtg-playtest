"""Small CLI input conveniences. No game rules or implicit active session."""
import json
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[4]


def session_file(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise SystemExit('対局の短縮名は英小文字・数字・_・-で指定してください。')
    return ROOT / 'playtest' / '.sessions' / (name + '.json')


def register(args):
    path = session_file(args.name)
    config = {key: str(pathlib.Path(value).resolve()) for key, value in {
        'state': args.state, 'results': args.results or pathlib.Path(args.state).parent / 'results.jsonl',
        'cards_dir': args.cards_dir, 'decks_dir': args.decks_dir}.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise SystemExit('短縮名は登録済みです。別の名前を使ってください: ' + args.name)
    print('対局 %s → %s' % (args.name, config['state']))


def expand(argv):
    """Resolve an explicit alias before parsing. Batch children receive concrete paths."""
    argv = list(argv)
    # Only inspect global options; --session inside note/--do text is ordinary text.
    index = 0
    valued = {'--state', '--results', '--cards-dir', '--decks-dir', '--as'}
    flags = {'--offline', '--en'}
    while index < len(argv):
        word = argv[index]
        if word == '--session' or word.startswith('--session='):
            if word == '--session':
                if index + 1 == len(argv):
                    raise SystemExit('--sessionには短縮名が必要です。')
                name, size = argv[index + 1], 2
            else:
                name, size = word.split('=', 1)[1], 1
            try:
                config = json.loads(session_file(name).read_text(encoding='utf-8'))
            except FileNotFoundError:
                raise SystemExit('未登録の対局短縮名: ' + name)
            rest = argv[:index] + argv[index + size:]
            # Explicit paths cannot silently redirect a named session.
            for item in rest:
                if item.split('=', 1)[0] in valued - {'--as'} or item.split('=', 1)[0] == '--session':
                    raise SystemExit('--sessionとパス指定は併用できません。')
            options = []
            for key in ('state', 'results', 'cards_dir', 'decks_dir'):
                options += ['--' + key.replace('_', '-'), config[key]]
            return options + rest
        if word in valued:
            index += 2
        elif word in flags or word.split('=', 1)[0] in valued:
            index += 1
        else:
            break
    return argv


def save_commands(state, text):
    """Persist submitted commands once, without overwriting prior evidence."""
    state = pathlib.Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    numbers = [int(m[1]) for p in state.parent.glob(state.stem + '-*.mtg')
               if (m := re.fullmatch(re.escape(state.stem) + r'-(\d+)\.mtg', p.name))]
    number = max(numbers, default=0) + 1
    while True:
        path = state.parent / ('%s-%03d.mtg' % (state.stem, number))
        try:
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
            return path
        except FileExistsError:
            number += 1
