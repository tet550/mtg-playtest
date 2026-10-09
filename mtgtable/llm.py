"""OpenAI の API で、審判と AI の席を回す（prompt.py が作るプロンプトを送り、返答を卓に書く）。

標準ライブラリ（urllib）だけで Chat Completions（POST /v1/chat/completions）を呼ぶ。

キー: 環境変数 OPENAI_API_KEY か、リポジトリ直下の secrets/openai_api_key（1行。git 管理外）。
設定ファイル secrets/openai.json（省略可。git 管理外）:
    {"model": "gpt-5",                  全部の役割の既定のモデル
     "judge_model": "...",              審判だけ別のモデルにする（省略で model）
     "player_model": "...",             AI の席だけ別のモデルにする（省略で model）
     "reasoning_effort": "medium"}      推論モデルの reasoning_effort（low / medium / high。省略で送らない）
環境変数（設定ファイルより優先。どれも省略可）:
    MTGTABLE_OPENAI_MODEL             モデル（全部の役割。auto --model も同じ）
    MTGTABLE_OPENAI_REASONING_EFFORT  reasoning_effort
    OPENAI_BASE_URL                   API の場所（既定 https://api.openai.com/v1。テストや互換サーバー用）
使えるモデルはアカウントによる。何も指定しなければ DEFAULT_MODEL。

プロンプト・キャッシュ: OpenAI は、先頭が同じ長いプロンプト（1024 トークン以上）を自動でキャッシュする。prompt.py は
固定部分（役割の指示・リファレンス・デッキ）を先頭の system に、毎回変わる部分を最後の user に置くので、そのまま効く。
効いたかは usage.prompt_tokens_details.cached_tokens で分かる（prompts/<役割>/usage.jsonl に残す）。

調査用の記録: API を呼ぶたびに prompts/ai_log.jsonl へ1行足す（役割・プロンプトの stem・モデル・かかった秒数・
finish_reason・usage・返答の全文・卓に書けたか / 失敗の理由・作り直したプロンプトの stem）。API を呼べなかったときも残す。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import play, prompt
from .store import GameStore

DEFAULT_MODEL = "gpt-5"
KEY_FILE = pathlib.Path(__file__).resolve().parents[1] / "secrets" / "openai_api_key"
CONFIG_FILE = KEY_FILE.with_name("openai.json")
CONFIG_KEYS = ("model", "judge_model", "player_model", "reasoning_effort")
TIMEOUT = 600  # 1回の応答を待つ秒数（推論モデルは長く考えることがある）
RETRIES = 3  # 429・5xx・通信の失敗を、この回数まで待ってやり直す


class LLMError(Exception):
    """API を呼べなかった（キーが無い・断られた・通信できない）。"""


def api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise LLMError("OpenAI の API キーが無い: %s に書くか、環境変数 OPENAI_API_KEY を設定する" % KEY_FILE)
    return key


def config() -> dict:
    """secrets/openai.json の中身（無ければ空）。知らないキーや文字列でない値は断る（書き間違いに気づけるように）。"""
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise LLMError("%s が JSON として読めない: %s" % (CONFIG_FILE, e))
    if not isinstance(data, dict):
        raise LLMError("%s は {\"model\": \"...\"} の形にする" % CONFIG_FILE)
    unknown = [k for k in data if k not in CONFIG_KEYS]
    if unknown or not all(isinstance(v, str) for v in data.values()):
        raise LLMError("%s: 使えるキーは %s（値は文字列）。知らないキー: %s"
                       % (CONFIG_FILE, ", ".join(CONFIG_KEYS), ", ".join(unknown) or "なし"))
    return {k: v.strip() for k, v in data.items() if v.strip()}


def model(role: Optional[str] = None) -> str:
    """役割（"judge" か Player の席。省略で共通）のモデル。環境変数 → 設定ファイル（役割別 → 共通）→ 既定 の順。"""
    env = os.environ.get("MTGTABLE_OPENAI_MODEL", "").strip()
    if env:
        return env
    cfg = config()
    specific = cfg.get("judge_model" if role == play.JUDGE else "player_model") if role else None
    return specific or cfg.get("model") or DEFAULT_MODEL


def reasoning_effort() -> Optional[str]:
    return os.environ.get("MTGTABLE_OPENAI_REASONING_EFFORT", "").strip() or config().get("reasoning_effort")


def base_url() -> str:
    return (os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")


def request_body(system: list, user: str, role: Optional[str] = None) -> dict:
    """Chat Completions の本文。固定部分を先頭に（キャッシュが効くように、毎回同じバイト列で）。"""
    body = {"model": model(role),
            "messages": [{"role": "system", "content": s} for s in system] + [{"role": "user", "content": user}]}
    effort = reasoning_effort()
    if effort:
        body["reasoning_effort"] = effort
    return body


def complete(body: dict) -> dict:
    """本文を送り、{"text", "usage", "model"} を返す。429・5xx・通信の失敗は少し待ってやり直す。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key()}
    last = None
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(base_url() + "/chat/completions", data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                res = json.loads(r.read().decode("utf-8"))
            choice = (res.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            if not text:
                raise LLMError("empty response (finish_reason: %s)" % choice.get("finish_reason"))
            return {"text": text, "usage": res.get("usage") or {}, "model": res.get("model", body["model"]),
                    "finish_reason": choice.get("finish_reason")}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code != 429 and e.code < 500:
                raise LLMError("OpenAI API %d: %s" % (e.code, detail))
            last = "OpenAI API %d: %s" % (e.code, detail)
            wait = float(e.headers.get("retry-after") or 2 ** attempt * 2)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = "OpenAI API に接続できない: %s" % e
            wait = 2 ** attempt * 2
        if attempt < RETRIES:
            time.sleep(min(wait, 60))
    raise LLMError(last)


def _log_usage(built: dict, res: dict) -> None:
    rec = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "stem": built["stem"],
           "model": res["model"], "usage": res["usage"]}
    with open(built["dir"] / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ai_log_path(store: GameStore) -> pathlib.Path:
    return store.root / "prompts" / "ai_log.jsonl"


def _log_call(store: GameStore, built: dict, started: float, res: Optional[dict] = None,
              out: Optional[dict] = None, error: Optional[str] = None) -> None:
    """調査用に、1回の呼び出し（送ったプロンプト・返答・適用の結果）を prompts/ai_log.jsonl に1行で残す。"""
    rec = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "role": built["role"], "stem": built["stem"],
           "cursor": built["cursor"], "request": "prompts/%s/%s.request.json" % (built["role"], built["stem"]),
           "seconds": round(time.time() - started, 1)}
    if res:
        rec.update({"model": res["model"], "finish_reason": res.get("finish_reason"), "usage": res["usage"],
                    "response": res["text"]})
    if out is not None:
        rec["ok"] = out["ok"]
        if out.get("error"):
            rec["error"] = out["error"]
        if out.get("next"):
            rec["retry_stem"] = out["next"]["stem"]  # 失敗の理由を付けて作り直したプロンプト
        stopped = (out.get("result") or {}).get("stopped")
        if stopped:
            rec["stopped"] = stopped
    if error:
        rec.update({"ok": False, "error": error})
    path = ai_log_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def step(store: GameStore, ai: Optional[list] = None) -> Optional[dict]:
    """審判か AI の席の番なら、プロンプトを作って送り、返答を卓に書く。人間の番・決着なら None。"""
    ai = prompt.ai_seats(store) if ai is None else ai
    play.autopass(store)  # 止める場所に当たらない応答の機会は、API を呼ばず・人間に聞かずにパス
    role = prompt.next_role(store, ai)
    if not role:
        return None
    built = prompt.build(store, role)
    started = time.time()
    try:
        res = complete(json.loads(built["request"].read_text(encoding="utf-8")))
    except LLMError as e:
        _log_call(store, built, started, error=str(e))
        raise
    _log_usage(built, res)
    try:
        out = prompt.apply_response(store, res["text"], role)
    except Exception as e:
        _log_call(store, built, started, res, error="%s: %s" % (type(e).__name__, e))
        raise
    _log_call(store, built, started, res, out)
    out["usage"], out["text"] = res["usage"], res["text"]
    return out


class Changes:
    """対局が変わったら起こしてもらう。サーバー（serve）の更新通知（SSE /api/games/<対局>/events）につなぎ、通知が届いたら
    すぐ起きる。つながらない間は interval 秒ごとの見張りに戻り、retry 秒ごとにつなぎ直す。"""

    def __init__(self, game: str, server: Optional[str], interval: float = 1.0, retry: float = 10.0, report=print):
        self.url = "%s/api/games/%s/events" % (server.rstrip("/"), urllib.parse.quote(game)) if server else None
        self.interval, self.retry, self.report = interval, retry, report
        self.connected = False
        self._event = threading.Event()
        if self.url:
            threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self) -> None:
        while True:
            try:
                with urllib.request.urlopen(self.url, timeout=60) as r:  # サーバーは 15 秒ごとに keep-alive を送る
                    if not self.connected:
                        self.report("サーバーの更新通知で待つ（%s）" % self.url)
                    self.connected = True
                    for line in r:
                        if line.startswith(b"data:"):
                            self._event.set()
            except (urllib.error.URLError, OSError, ValueError):
                pass
            if self.connected:
                self.report("サーバーの更新通知が切れた。%g 秒ごとの見張りで待つ" % self.interval)
            self.connected = False
            self._event.set()  # 切れた間の変更を取りこぼさないよう、一度見に行く
            time.sleep(self.retry)

    def wait(self) -> None:
        """変わるまで（つながっていなければ interval 秒）待つ。"""
        self._event.wait(None if self.connected else self.interval)
        self._event.clear()


def run(store: GameStore, ai: Optional[list] = None, watch: bool = False, max_failures: int = 3,
        interval: float = 1.0, report=print, server: Optional[str] = None) -> str:
    """審判と AI の席を、人間の番になるまで（watch なら決着まで、人間の操作を待ちながら）回す。止まった理由を返す。
    watch では、server（serve の URL）の更新通知で待つ。つながらなければ interval 秒ごとに見る。"""
    failures, escalated = 0, set()  # escalated: 審判に回した後、まだ一度も返答が通っていない席
    changes = Changes(store.name, server, interval, report=report) if watch else None
    while True:
        out = step(store, ai)
        if out is None:
            wait = play.waiting_on(store.load())
            if wait is None:
                return "決着"
            if not watch:
                return "waiting on %s（人間の番）" % wait
            changes.wait()
            continue
        cached = ((out.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
        report("%s: %s%s" % (out["role"], "ok" if out["ok"] else "failed: " + (out["error"] or ""),
                             "（cached %s tokens）" % cached if cached else ""))
        failures = 0 if out["ok"] else failures + 1
        if out["ok"]:
            escalated.discard(out["role"])
        if failures >= max_failures and out["role"] != play.JUDGE and out["role"] not in escalated:
            # AI の席が出せずに詰まった: 止める前に一度だけ、その席の名前で審判に回す（審判が状況を見て判断する）
            esc = prompt.escalate(store, out["role"], out.get("error") or "", out.get("text") or "")
            report("%s: %s" % (out["role"], "審判に回した" if esc["ok"] else "審判に回せなかった: " + (esc["error"] or "")))
            if esc["ok"]:
                escalated.add(out["role"])
                failures = 0
                continue
        if failures >= max_failures:
            return "%s の返答が %d 回続けて適用できなかったので止めた（prompts/%s/ の response を確かめる）" % (
                out["role"], failures, out["role"])
