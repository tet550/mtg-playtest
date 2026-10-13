"""mtgtable — AI が紙の MTG をプレイするためのデジタル卓（Table Engine）。

設計: design/basic_design.md
"""
__version__ = "0.1.0"  # 配布物の版（pyproject.toml はここを読む）。下の import より前に置く（carddb が読む）

from .engine import Engine, normalize_batch, public_result
from .info import player_view
from .model import GameState
from .operations import OperationError
from .setup import load_decklist, new_game, parse_decklist
from .store import GameStore

__all__ = ["__version__", "Engine", "GameState", "GameStore", "OperationError", "load_decklist", "new_game",
           "normalize_batch", "parse_decklist", "player_view", "public_result"]
