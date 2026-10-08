"""中核ゲームモデル（design/basic_design.md 30節）。

Table Engine が保持するのは卓上の物理的・構造的な状態だけで、カードテキストや
ルールの意味は持たない。すべてのモデルは JSON に落とせる素の値だけで構成する。
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Optional

SCHEMA = "mtgtable/state@2"

# ---------------------------------------------------------------- Zone

# 公開範囲: public = 全員, owner = 持ち主のみ, hidden = 誰も見えない
PLAYER_ZONES = {
    "library": {"ordered": True, "visibility": "hidden"},
    "hand": {"ordered": False, "visibility": "owner"},
    "graveyard": {"ordered": True, "visibility": "public"},
    # ゲーム外。サイドボードや「ゲームの外部」から持ってくるカード置き場
    "sideboard": {"ordered": False, "visibility": "owner"},
}
SHARED_ZONES = {
    "battlefield": {"ordered": False, "visibility": "public"},
    "stack": {"ordered": True, "visibility": "public"},
    "exile": {"ordered": False, "visibility": "public"},
    "command": {"ordered": False, "visibility": "public"},
}

# ---------------------------------------------------------------- Turn

TURN_SEQUENCE = [
    ("beginning", "untap"),
    ("beginning", "upkeep"),
    ("beginning", "draw"),
    ("main1", "main"),
    ("combat", "beginning_of_combat"),
    ("combat", "declare_attackers"),
    ("combat", "declare_blockers"),
    ("combat", "combat_damage"),
    ("combat", "end_of_combat"),
    ("main2", "main"),
    ("ending", "end"),
    ("ending", "cleanup"),
]
# 通常は優先権が発生しないステップ。step はここで priority を空にする
NO_PRIORITY_STEPS = {"untap", "cleanup"}
# ゲーム前（マリガン・開始時の手札からの行動。総合ルール 103）。turn 0。最初の step で先攻の T1 に入る
PREGAME = ("pregame", "pregame")

INFO_POLICIES = ("normal", "own_library", "all_libraries", "omniscient")


@dataclass
class Zone:
    name: str
    kind: str
    owner: Optional[str]
    ordered: bool
    visibility: str
    cards: list = field(default_factory=list)  # 順序付きなら index 0 が一番上


@dataclass
class Card:
    id: str
    name: str
    owner: str
    controller: str
    zone: str
    definition: dict = field(default_factory=dict)  # 解釈しない。カード名やトークンの定義
    token: bool = False
    tapped: bool = False
    face_down: bool = False
    face: int = 0  # 両面カード等で上を向いている面
    entered_at: int = 0  # 現在の領域に入った時刻。領域を移ると新しい値になる
    # 印刷されたタイプ行（オラクルから）。解釈はせず、表示の並べ替えと召喚酔いの表示にだけ使う
    type_line: str = ""
    controlled_since: int = 0  # 今のコントローラーが戦場でコントロールし始めた時刻（召喚酔いの表示用）


@dataclass
class Counter:
    target: str
    kind: str
    amount: int


@dataclass
class Note:
    id: str
    target: str
    text: str
    until: Optional[str] = None  # "end_of_turn" など。Table Engine は解釈しない
    turn: int = 0


@dataclass
class Link:
    id: str
    kind: str
    source: str
    targets: list = field(default_factory=list)
    text: str = ""


@dataclass
class Mana:
    id: str
    color: str  # W U B R G C など。Table Engine は解釈しない
    amount: int
    source: Optional[str] = None
    duration: Optional[str] = None


@dataclass
class ManaPool:
    mana: list = field(default_factory=list)  # list[Mana]


@dataclass
class Player:
    id: str
    name: str
    life: int = 20
    status: str = "playing"  # playing / conceded / lost / won / draw
    mana_pool: ManaPool = field(default_factory=ManaPool)


@dataclass
class StackItem:
    id: str
    kind: str  # spell / activated / triggered / other
    controller: str
    card: Optional[str] = None  # スタック領域にあるカード（呪文など）
    source: Optional[str] = None  # 能力の発生源
    text: str = ""


@dataclass
class Stack:
    items: list = field(default_factory=list)  # index 0 が一番上


@dataclass
class AttackAssignment:
    attacker: str
    target: str


@dataclass
class BlockAssignment:
    blocker: str
    attacker: str


@dataclass
class Combat:
    attacks: list = field(default_factory=list)
    blocks: list = field(default_factory=list)


@dataclass
class TurnState:
    turn: int = 1
    active: Optional[str] = None
    phase: str = "beginning"
    step: str = "untap"
    priority: Optional[str] = None
    passed: list = field(default_factory=list)  # 何もせずに続けて優先権をパスした Player


@dataclass
class Declaration:
    seq: int
    player: str
    kind: str  # pass / concede / その他
    text: str = ""
    turn: int = 0
    step: str = ""
    phase: str = ""  # main1 と main2 はどちらも step が main なので、区別に使う
    choices: list = field(default_factory=list)  # 審判の質問（ask）の選択肢。空なら自由記述
    cards: list = field(default_factory=list)  # ask: 選ぶ候補のカード。answer: 選んだカード
    pick: list = field(default_factory=list)  # ask で cards があるとき、選ぶ枚数 [最小, 最大]


# ---------------------------------------------------------------- GameState

@dataclass
class GameState:
    seed: int = 0
    version: int = 0
    clock: int = 0
    player_order: list = field(default_factory=list)
    players: dict = field(default_factory=dict)
    cards: dict = field(default_factory=dict)
    zones: dict = field(default_factory=dict)
    counters: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)
    links: dict = field(default_factory=dict)
    stack: Stack = field(default_factory=Stack)
    combat: Combat = field(default_factory=Combat)
    turn: TurnState = field(default_factory=TurnState)
    declarations: list = field(default_factory=list)
    # KnownInformation: player -> card id -> {"ordered": bool}
    #   ordered = 順序付き非公開領域での位置まで知っているか（シャッフルで False になる）
    knowledge: dict = field(default_factory=dict)
    info_policy: dict = field(default_factory=dict)
    # 継続的なパスの宣言: player -> {"until": stack/step/turn, "turn", "step", "stack_seen"}
    standing_passes: dict = field(default_factory=dict)
    turn_started: dict = field(default_factory=dict)  # player -> その Player の直近のターンが始まった時刻
    next_ids: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # ---- id

    def new_id(self, prefix: str) -> str:
        """オブジェクトの id。読みやすさのため必ず # を付ける（#c12, #s3 など）。"""
        n = self.next_ids.get(prefix, 1)
        self.next_ids[prefix] = n + 1
        return "#%s%d" % (prefix, n)

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    # ---- lookup

    def add_player(self, pid: str, name: str, life: int = 20) -> Player:
        p = Player(id=pid, name=name, life=life)
        self.players[pid] = p
        self.player_order.append(pid)
        self.knowledge.setdefault(pid, {})
        self.info_policy.setdefault(pid, "normal")
        for zname, spec in PLAYER_ZONES.items():
            full = "%s.%s" % (pid, zname)
            self.zones[full] = Zone(name=full, kind=zname, owner=pid, **spec)
        return p

    def ensure_shared_zones(self) -> None:
        for zname, spec in SHARED_ZONES.items():
            if zname not in self.zones:
                self.zones[zname] = Zone(name=zname, kind=zname, owner=None, **spec)

    def zone_of(self, card_id: str) -> Zone:
        return self.zones[self.cards[card_id].zone]

    def find_mana(self, mana_id: str):
        for p in self.players.values():
            for m in p.mana_pool.mana:
                if m.id == mana_id:
                    return p, m
        return None, None

    def find_stack_item(self, item_id: str):
        for i, it in enumerate(self.stack.items):
            if it.id == item_id:
                return i, it
        return None, None

    def object_kind(self, oid: str) -> Optional[str]:
        """Counter / Note / Link の対象になれるオブジェクトの種類。"""
        if oid in self.cards:
            return "card"
        if oid in self.players:
            return "player"
        if self.find_mana(oid)[1] is not None:
            return "mana"
        if self.find_stack_item(oid)[1] is not None:
            return "stack_item"
        if oid in self.links:
            return "link"
        if oid in self.notes:
            return "note"
        return None

    def counters_on(self, target: str) -> dict:
        return {c.kind: c.amount for c in self.counters if c.target == target}

    def notes_on(self, target: str) -> list:
        return [n for n in self.notes.values() if n.target == target]

    def links_of(self, oid: str) -> list:
        return [l for l in self.links.values() if l.source == oid or oid in l.targets]

    # ---- serialization

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        d = copy.deepcopy(d)
        d.pop("schema", None)
        s = cls()
        for k in ("seed", "version", "clock", "player_order"):
            if k in d:
                setattr(s, k, d[k])
        s.players = {}
        for pid, p in d.get("players", {}).items():
            pool = ManaPool(mana=[Mana(**m) for m in p.get("mana_pool", {}).get("mana", [])])
            s.players[pid] = Player(id=p["id"], name=p["name"], life=p["life"],
                                    status=p.get("status", "playing"), mana_pool=pool)
        s.cards = {cid: Card(**c) for cid, c in d.get("cards", {}).items()}
        s.zones = {zn: Zone(**z) for zn, z in d.get("zones", {}).items()}
        s.counters = [Counter(**c) for c in d.get("counters", [])]
        s.notes = {nid: Note(**n) for nid, n in d.get("notes", {}).items()}
        s.links = {lid: Link(**l) for lid, l in d.get("links", {}).items()}
        s.stack = Stack(items=[StackItem(**i) for i in d.get("stack", {}).get("items", [])])
        cb = d.get("combat", {})
        s.combat = Combat(attacks=[AttackAssignment(**a) for a in cb.get("attacks", [])],
                          blocks=[BlockAssignment(**b) for b in cb.get("blocks", [])])
        s.turn = TurnState(**d.get("turn", {}))
        s.declarations = [Declaration(**x) for x in d.get("declarations", [])]
        s.knowledge = d.get("knowledge", {})
        s.info_policy = d.get("info_policy", {})
        s.standing_passes = d.get("standing_passes", {})
        s.turn_started = d.get("turn_started", {})
        s.next_ids = d.get("next_ids", {})
        s.meta = d.get("meta", {})
        return s

    def clone(self) -> "GameState":
        return copy.deepcopy(self)


def zone_name(owner: Optional[str], kind: str) -> str:
    return kind if kind in SHARED_ZONES else "%s.%s" % (owner, kind)


def is_player_zone_kind(kind: str) -> bool:
    return kind in PLAYER_ZONES

