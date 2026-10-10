"""置き場所（design/app_plan.md 段階 1）。パッケージの外を指す場所は、ここだけで決める。

- 読むだけのもの（同梱のデッキ・プレイ方針・審判のリファレンス）: リポジトリから動いている時はリポジトリの中の今の場所
  （`decklists/`・`.claude/skills/mtg-playtest/references/`）。パッケージとして入れた時はパッケージの中の `assets/`
  （ビルドで写す。段階 2）
- 書くもの（カードのキャッシュ・API キー・対局フォルダ）: data_home() の下
  1. 環境変数 MTGTABLE_HOME
  2. リポジトリから動いている時はリポジトリ直下（今までと同じ `cards/`・`secrets/`・`playtest/`）
  3. OS のデータ用フォルダ（macOS ~/Library/Application Support/mtgtable、Windows %APPDATA%\\mtgtable、
     それ以外 $XDG_DATA_HOME/mtgtable か ~/.local/share/mtgtable）

「リポジトリから動いている」は、パッケージの一つ上に .git か pyproject.toml があること（clone・worktree・pip install -e）。
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


def secrets_dir() -> pathlib.Path:
    """API キーと AI の設定。"""
    return data_home() / "secrets"


def games_dir() -> pathlib.Path:
    """対局フォルダの既定の置き場所（serve の --root）。"""
    return data_home() / "playtest"


# ---------------------------------------------------------------- 読むだけのもの

def decklists_dir() -> pathlib.Path:
    """同梱のデッキリスト（*.txt）と、そのプレイ方針（strategy/）。"""
    root = checkout()
    return root / "decklists" if root else ASSETS / "decklists"


def references_dir() -> pathlib.Path:
    """審判と AI の席のプロンプトに入れるリファレンス（cli.md・rules.md・patterns.md）。正本はスキルの中。"""
    root = checkout()
    return root / ".claude" / "skills" / "mtg-playtest" / "references" if root else ASSETS / "references"
