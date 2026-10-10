"""審判と AI の席を、LLM の API で回す（prompt.py が作るプロンプトを送り、返答を卓に書く）。

提供元（provider）は差し替えられる。役割（審判・AI の席）ごとに別の提供元・モデルにもできる。
- openai: Chat Completions（POST /v1/chat/completions）。標準ライブラリ（urllib）だけで呼ぶ
- anthropic: Claude の Messages API。公式の SDK（pip install anthropic）で呼ぶ。この提供元を選んだときだけ読み込むので、
  使わなければ標準ライブラリだけで動く

キー:
- openai: 環境変数 OPENAI_API_KEY か、secrets/openai_api_key（1行。git 管理外）
- anthropic: 環境変数 ANTHROPIC_API_KEY か、secrets/anthropic_api_key。どちらも無ければ SDK が自分で探す
  （ANTHROPIC_AUTH_TOKEN・`ant auth login` のプロファイルなど）
設定ファイル secrets/llm.json（無ければ前の名前の secrets/openai.json。省略可。git 管理外）:
    {"provider": "anthropic",           全部の役割の既定の提供元（openai / anthropic。省略で openai）
     "judge_provider": "...",           審判だけ別の提供元に（省略で provider）
     "player_provider": "...",          AI の席だけ別の提供元に
     "model": "...",                    全部の役割の既定のモデル（省略で提供元の既定 DEFAULT_MODELS）
     "judge_model": "...", "player_model": "...",
     "effort": "medium",                考える深さ（anthropic の output_config.effort: low〜max。openai の reasoning_effort にも使う）
     "reasoning_effort": "medium"}      前の名前（openai の reasoning_effort）
環境変数（設定ファイルより優先。どれも省略可）:
    MTGTABLE_LLM_PROVIDER             提供元（全部の役割）
    MTGTABLE_MODEL                    モデル（全部の役割。auto --model も同じ）。前の名前 MTGTABLE_OPENAI_MODEL も使える
    MTGTABLE_OPENAI_REASONING_EFFORT  openai の reasoning_effort
    OPENAI_BASE_URL / ANTHROPIC_BASE_URL  API の場所（テストや互換サーバー用）

プロンプト・キャッシュ: prompt.py は固定部分（役割の指示・リファレンス・デッキ）を先頭の system に、毎回変わる部分を
最後の user に置く。openai は先頭が同じ長いプロンプトを自動でキャッシュする。anthropic では system の2つの区切りに
cache_control（TTL 1時間。人間の相手は1手に5分以上かけることがあるので）を付ける。効いた量は usage の cached_tokens
（どの提供元でも同じ形に直して prompts/<役割>/usage.jsonl に残す）。

anthropic の断り（stop_reason: refusal）: サーバー側の代わりのモデル（fallbacks: "default"）を有効にしている。
それでも断られたら LLMError（理由の分類つき）。

調査用の記録: API を呼ぶたびに prompts/ai_log.jsonl へ1行足す（役割・プロンプトの stem・提供元・モデル・かかった秒数・
終わり方・usage・返答の全文・卓に書けたか / 失敗の理由・作り直したプロンプトの stem）。API を呼べなかったときも残す。
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

# run が返す止まった理由のうち、決着（worker はこの値と比べる。表示の文を比べない）
GAME_OVER = "決着"
PROVIDERS = ("openai", "anthropic")
DEFAULT_PROVIDER = "openai"
DEFAULT_MODELS = {"openai": "gpt-5", "anthropic": "claude-opus-5-5"}
DEFAULT_MODEL = DEFAULT_MODELS[DEFAULT_PROVIDER]
SECRETS = pathlib.Path(__file__).resolve().parents[1] / "secrets"
KEY_FILE = SECRETS / "openai_api_key"
ANTHROPIC_KEY_FILE = SECRETS / "anthropic_api_key"
LLM_CONFIG_FILE = SECRETS / "llm.json"
CONFIG_FILE = SECRETS / "openai.json"  # 前の名前（llm.json が無ければこちら）
CONFIG_KEYS = ("provider", "judge_provider", "player_provider", "model", "judge_model", "player_model",
               "effort", "reasoning_effort")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TIMEOUT = 600  # 1回の応答を待つ秒数（推論モデルは長く考えることがある）
RETRIES = 3  # 429・5xx・通信の失敗を、この回数まで待ってやり直す
ANTHROPIC_MAX_TOKENS = 16000  # 審判の Batch も収まる（返答は1通の JSON）
ANTHROPIC_BETAS = ["server-side-fallback-2026-07-01"]  # fallbacks: "default"（断られたら、分類に合う別のモデルで）
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")  # fallbacks を受けるモデル


class LLMError(Exception):
    """API を呼べなかった（キーが無い・断られた・通信できない）。"""


def config_file() -> pathlib.Path:
    return LLM_CONFIG_FILE if LLM_CONFIG_FILE.exists() else CONFIG_FILE


def config() -> dict:
    """設定ファイルの中身（無ければ空）。知らないキーや文字列でない値は断る（書き間違いに気づけるように）。"""
    path = config_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise LLMError("%s が JSON として読めない: %s" % (path, e))
    if not isinstance(data, dict):
        raise LLMError("%s は {\"model\": \"...\"} の形にする" % path)
    unknown = [k for k in data if k not in CONFIG_KEYS]
    if unknown or not all(isinstance(v, str) for v in data.values()):
        raise LLMError("%s: 使えるキーは %s（値は文字列）。知らないキー: %s"
                       % (path, ", ".join(CONFIG_KEYS), ", ".join(unknown) or "なし"))
    out = {k: v.strip() for k, v in data.items() if v.strip()}
    for k in ("provider", "judge_provider", "player_provider"):
        if out.get(k) and out[k] not in PROVIDERS:
            raise LLMError("%s: %s は %s のどれか" % (path, k, " / ".join(PROVIDERS)))
    return out


def _per_role(cfg: dict, key: str, role: Optional[str]) -> Optional[str]:
    if not role:
        return cfg.get(key)
    return cfg.get(("judge_" if role == play.JUDGE else "player_") + key) or cfg.get(key)


def provider(role: Optional[str] = None) -> str:
    """役割（"judge" か Player の席。省略で共通）の提供元。環境変数 → 設定ファイル（役割別 → 共通）→ 既定 の順。"""
    env = os.environ.get("MTGTABLE_LLM_PROVIDER", "").strip()
    if env:
        if env not in PROVIDERS:
            raise LLMError("MTGTABLE_LLM_PROVIDER は %s のどれか" % " / ".join(PROVIDERS))
        return env
    return _per_role(config(), "provider", role) or DEFAULT_PROVIDER


def model(role: Optional[str] = None) -> str:
    """役割のモデル。環境変数 → 設定ファイル（役割別 → 共通）→ その提供元の既定 の順。"""
    env = (os.environ.get("MTGTABLE_MODEL", "") or os.environ.get("MTGTABLE_OPENAI_MODEL", "")).strip()
    if env:
        return env
    return _per_role(config(), "model", role) or DEFAULT_MODELS[provider(role)]


def effort() -> Optional[str]:
    value = os.environ.get("MTGTABLE_OPENAI_REASONING_EFFORT", "").strip() or config().get("effort") \
        or config().get("reasoning_effort")
    if value and value not in EFFORTS:
        raise LLMError("effort は %s のどれか" % " / ".join(EFFORTS))
    return value


reasoning_effort = effort  # 前の名前


def api_key(name: Optional[str] = None) -> Optional[str]:
    """提供元のキー。openai は必須（無ければ LLMError）。anthropic は見つからなければ None（SDK が自分で探す）。"""
    name = name or provider()
    if name == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key and ANTHROPIC_KEY_FILE.exists():
            key = ANTHROPIC_KEY_FILE.read_text(encoding="utf-8").strip()
        return key or None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise LLMError("OpenAI の API キーが無い: %s に書くか、環境変数 OPENAI_API_KEY を設定する" % KEY_FILE)
    return key


def check_ready(roles=(play.JUDGE, "p")) -> None:
    """使う提供元のキー・SDK がそろっているか（auto・serve の起動時。足りなければ LLMError）。"""
    for name in {provider(r) for r in roles}:
        api_key(name)
        if name == "anthropic":
            _anthropic()


def base_url() -> str:
    return (os.environ.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1").rstrip("/")


def request_body(system: list, user: str, role: Optional[str] = None) -> dict:
    """API の本文（その役割の提供元の形）。固定部分を先頭に（キャッシュが効くように、毎回同じバイト列で）。"""
    name, e = provider(role), effort()
    if name == "anthropic":
        body = {"model": model(role), "max_tokens": ANTHROPIC_MAX_TOKENS,
                "system": [{"type": "text", "text": s, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
                           for s in system],
                "messages": [{"role": "user", "content": user}]}
        if body["model"] in FALLBACK_MODELS:
            body.update({"betas": ANTHROPIC_BETAS, "fallbacks": "default"})
        if e:
            body["output_config"] = {"effort": e}
        return body
    body = {"model": model(role),
            "messages": [{"role": "system", "content": s} for s in system] + [{"role": "user", "content": user}]}
    if e:
        body["reasoning_effort"] = "high" if e in ("xhigh", "max") else e
    return body


def body_provider(body: dict) -> str:
    """本文がどの提供元の形か（anthropic の本文には max_tokens と system の区切りがある）。"""
    return "anthropic" if "max_tokens" in body and isinstance(body.get("system"), list) else "openai"


def complete(body: dict, name: Optional[str] = None) -> dict:
    """本文を送り、{"text", "usage", "model", "finish_reason", "provider"} を返す。usage はどの提供元でも
    {"input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "total_tokens", "raw"}。"""
    name = name or body_provider(body)
    res = _complete_anthropic(body) if name == "anthropic" else _complete_openai(body)
    res["provider"] = name
    return res


def _usage(input_tokens: int, output_tokens: int, cached: int, written: int, raw: dict) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cached_tokens": cached,
            "cache_write_tokens": written, "total_tokens": input_tokens + output_tokens, "raw": raw}


def _complete_openai(body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + api_key("openai")}
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
            u = res.get("usage") or {}
            usage = _usage(u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0,
                           (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0, 0, u)
            return {"text": text, "usage": usage, "model": res.get("model", body["model"]),
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


def _anthropic():
    try:
        import anthropic
    except ImportError:
        raise LLMError("provider anthropic には公式の SDK が要る: pip install anthropic")
    return anthropic


def _complete_anthropic(body: dict) -> dict:
    """Claude の Messages API（公式の SDK。429・5xx・通信の失敗は SDK が RETRIES 回までやり直す）。"""
    anthropic = _anthropic()
    key = api_key("anthropic")
    client = anthropic.Anthropic(**({"api_key": key} if key else {}), max_retries=RETRIES, timeout=TIMEOUT)
    try:
        msg = client.beta.messages.create(**body)
    except anthropic.APIConnectionError as e:
        raise LLMError("Anthropic API に接続できない: %s" % e)
    except anthropic.RateLimitError as e:
        raise LLMError("Anthropic API 429（混んでいる・上限）: %s" % e.message)
    except anthropic.APIStatusError as e:  # 400・401・403・404・5xx（やり直しても通らなかった）
        raise LLMError("Anthropic API %d: %s" % (e.status_code, e.message))
    if msg.stop_reason == "refusal":
        d = msg.stop_details
        raise LLMError("Claude が断った（分類: %s）%s" % (getattr(d, "category", None) or "不明",
                                                    (": " + d.explanation) if d and d.explanation else ""))
    text = "".join(b.text for b in msg.content if b.type == "text")
    if not text:
        raise LLMError("empty response (stop_reason: %s)" % msg.stop_reason)
    u = msg.usage
    cached, written = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    usage = _usage((u.input_tokens or 0) + cached + written, u.output_tokens or 0, cached, written, u.to_dict())
    return {"text": text, "usage": usage, "model": msg.model, "finish_reason": msg.stop_reason}


def _log_usage(built: dict, res: dict) -> None:
    rec = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "stem": built["stem"],
           "provider": res.get("provider"), "model": res["model"], "usage": res["usage"]}
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
        rec.update({"provider": res.get("provider"), "model": res["model"], "finish_reason": res.get("finish_reason"),
                    "usage": res["usage"],
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


def step(store: GameStore, ai: Optional[list] = None, guard=None) -> Optional[dict]:
    """審判か AI の席の番なら、プロンプトを作って送り、返答を卓に書く。人間の番・決着なら None。
    guard（サーバーの利用の上限。worker.Guard）があれば、呼ぶ前に check()（超えていれば例外）、呼んだ後に record(usage)。"""
    ai = prompt.ai_seats(store) if ai is None else ai
    play.autopass(store)  # 止める場所に当たらない応答の機会は、API を呼ばず・人間に聞かずにパス
    role = prompt.next_role(store, ai)
    if not role:
        return None
    if guard is not None:
        guard.check()
    built = prompt.build(store, role)
    started = time.time()
    try:
        res = complete(json.loads(built["request"].read_text(encoding="utf-8")))
    except LLMError as e:
        _log_call(store, built, started, error=str(e))
        raise
    if guard is not None:
        guard.record(res["usage"])
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
        interval: float = 1.0, report=print, server: Optional[str] = None, guard=None) -> str:
    """審判と AI の席を、人間の番になるまで（watch なら決着まで、人間の操作を待ちながら）回す。止まった理由を返す。
    watch では、server（serve の URL）の更新通知で待つ。つながらなければ interval 秒ごとに見る。"""
    failures, escalated = 0, set()  # escalated: 審判に回した後、まだ一度も返答が通っていない席
    changes = Changes(store.name, server, interval, report=report) if watch else None
    while True:
        out = step(store, ai, guard)
        if out is None:
            wait = play.waiting_on(store.load())
            if wait is None:
                return GAME_OVER
            if not watch:
                return "waiting on %s（人間の番）" % wait
            changes.wait()
            continue
        cached = (out.get("usage") or {}).get("cached_tokens")
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
