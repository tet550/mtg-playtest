"""mtgtable — AI が紙の MTG をプレイするためのデジタル卓（Table Engine）。

設計: design/basic_design.md
"""
from .engine import Engine, normalize_batch, public_result
from .info import player_view
from .model import GameState
from .operations import OperationError
from .setup import load_decklist, new_game, parse_decklist
from .store import GameStore, state_diff

__all__ = ["Engine", "GameState", "GameStore", "OperationError", "load_decklist", "new_game",
           "normalize_batch", "parse_decklist", "player_view", "public_result", "state_diff"]
