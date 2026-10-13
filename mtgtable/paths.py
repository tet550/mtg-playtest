"""置き場所（design/app_plan.md 段階 1）。パッケージの外を指す場所は、ここだけで決める。

- 読むだけのもの（同梱のデッキ・プレイ方針・審判のリファレンス）: リポジトリから動いている時はリポジトリの中の今の場所
  （`decklists/`・`plugins/mtgtable/skills/mtg-playtest/references/`）。パッケージとして入れた時はパッケージの中の `assets/`
  （ビルドで写す。段階 2）
- 書くもの（カードのキャッシュ・対局フォルダ・サイトの DB）: data_home() の下
  1. 環境変数 MTGTABLE_HOME
  2. リポジトリから動いている時はリポジトリ直下（今までと同じ `cards/`・`playtest/`・`data/`）
  3. OS のデータ用フォルダ（macOS ~/Library/Application Support/mtgtable、Windows %APPDATA%\\mtgtable、
     それ以外 $XDG_DATA_HOME/mtgtable か ~/.local/share/mtgtable）

「リポジトリから動いている」は、パッケージの一つ上に .git か pyproject.toml があること（clone・worktree・pip install -e）。

案内に出すコマンドの頭（command()）も、ここで決める。Claude Code のプラグインの起動（bin/mtgtable）からは `mtgtable`
（環境変数 MTGTABLE_CMD）、リポジトリからは `python -m mtgtable`、それ以外に入れたものは今動いている Python で `-m mtgtable`。
"""
from __future__ import annotations

import os
import pathlib
import sys
from typing import Optional

PACKAGE = pathlib.Path(__file__).resolve().parent
ASSETS = PACKAGE / "assets"
APP_NAME = "mtgtable"


def checkout() -> Optional[pathlib.Path]:
    """リポジトリから動いていれば、その直下。パッケージとして入れていれば None。"""
    root = PACKAGE.parent
    return root if (root / ".git").exists() or (root / "pyproject.toml").exists() else None


def _os_data_dir() -> pathlib.Path:
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return pathlib.Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / APP_NAME
    return pathlib.Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / APP_NAME


def data_home() -> pathlib.Path:
    env = os.environ.get("MTGTABLE_HOME", "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return checkout() or _os_data_dir()


# ---------------------------------------------------------------- 書くもの

def cards_dir() -> pathlib.Path:
    """カードのキャッシュ（環境変数 MTG_CARDS_DIR が優先）。"""
    env = os.environ.get("MTG_CARDS_DIR", "").strip()
    return pathlib.Path(env).expanduser() if env else data_home() / "cards"



def site_db() -> pathlib.Path:
    """手元のサイト（serve）の DB の既定の場所。プロンプトなどの作業ファイルは隣の site-files/ に置く（sqlstore）。"""
    return data_home() / "data" / "site.sqlite"


# ---------------------------------------------------------------- 読むだけのもの

def decklists_dir() -> pathlib.Path:
    """同梱のデッキリスト（*.txt）と、そのプレイ方針（strategy/）。"""
    root = checkout()
    return root / "decklists" if root else ASSETS / "decklists"


def references_dir() -> pathlib.Path:
    """審判と AI の席のプロンプトに入れるリファレンス（cli.md・rules.md・patterns.md）。正本はスキルの中。"""
    root = checkout()
    return root / "plugins" / "mtgtable" / "skills" / "mtg-playtest" / "references" if root else ASSETS / "references"


# ---------------------------------------------------------------- 案内

def command() -> str:
    """案内（serve の起動時・next の game: と wait: の行）に出す、mtgtable を起動するコマンドの頭。エージェントはこれをそのまま
    Bash で使う。入れたものは、uvx の一時的な環境でも同じ Python が残っているので、そのパスで指す（Git Bash で読める / 区切り）。"""
    env = os.environ.get("MTGTABLE_CMD", "").strip()
    if env:
        return env
    if checkout():
        return "python -m mtgtable"
    return "%s -m mtgtable" % shell_path(sys.executable)


def shell_path(path) -> str:
    """案内のコマンドに入れるパス。/ 区切りにして引用符で囲む（Git Bash では引用符の無い \\ が消え、別の名前のファイルができる）。"""
    return "'%s'" % str(path).replace("\\", "/")
