# VPS で公開する手順

VPS 1台に、Caddy（HTTPS・前段）と mtgtable（`serve --site`。127.0.0.1 で待ち受け）を置く。計画は
[design/site_plan.md](../design/site_plan.md)。例は Ubuntu 24.04、ドメインは `mtg.example.com`。

| ファイル | 置き場所 | 中身 |
|---|---|---|
| `Caddyfile` | `/etc/caddy/Caddyfile` | HTTPS（証明書の自動取得）と、127.0.0.1:8765 への中継 |
| `mtgtable.env.example` | `/etc/mtgtable/mtgtable.env` | DB の場所・運営者と連絡先・AI のキーと上限 |
| `mtgtable.service` | `/etc/systemd/system/` | サーバー（落ちたら起こし直す） |
| `mtgtable-backup.service` / `.timer` | `/etc/systemd/system/` | 毎日 4:20 に DB を写す（新しい 14 個を残す） |

## 1. 用意する

```bash
sudo adduser --system --group --home /var/lib/mtgtable mtgtable
sudo mkdir -p /opt/mtgtable /etc/mtgtable /var/lib/mtgtable/backups
sudo git clone <このリポジトリ> /opt/mtgtable
sudo python3 -m venv /opt/mtgtable/.venv
sudo /opt/mtgtable/.venv/bin/pip install anthropic   # AI の提供元に anthropic を使うときだけ
sudo chown -R mtgtable:mtgtable /var/lib/mtgtable
```

- Python 3.10 以上。mtgtable 自体は標準ライブラリだけで動く
- 対局フォルダで遊んでいた対局を移すなら: `python -m mtgtable --db /var/lib/mtgtable/mtg.sqlite db-import playtest/g1 ...`

## 2. 設定する

```bash
sudo cp /opt/mtgtable/deploy/mtgtable.env.example /etc/mtgtable/mtgtable.env
sudo chown mtgtable:mtgtable /etc/mtgtable/mtgtable.env && sudo chmod 600 /etc/mtgtable/mtgtable.env
sudoedit /etc/mtgtable/mtgtable.env   # 運営者・連絡先・AI のキー・上限
```

- `MTGTABLE_OPERATOR` / `MTGTABLE_CONTACT` は利用規約とプライバシーの画面に出る。空なら「未設定」と出る
- AI の提供元の管理画面でも、月の利用額の上限と通知を設定しておく（サイトの上限とは別の、最後の守り）

## 3. 動かす

```bash
sudo cp /opt/mtgtable/deploy/mtgtable.service /opt/mtgtable/deploy/mtgtable-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mtgtable mtgtable-backup.timer
curl -s http://127.0.0.1:8765/healthz      # {"ok": true, ...}

sudo apt install caddy
sudo cp /opt/mtgtable/deploy/Caddyfile /etc/caddy/Caddyfile   # ドメインを書き換える
sudo systemctl reload caddy
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

- アプリは 127.0.0.1 で待ち受け、外からは Caddy（443）だけが届く。`--trust-proxy` で、Caddy が付ける
  `X-Forwarded-For` を送り元の IP として使う（レート制限・アクセス・ログ）。Caddy を通さずに 0.0.0.0 で待ち受けない
- 起動時にキーと SDK を確かめ、足りなければ止まる（`journalctl -u mtgtable` に理由）

## 4. 公開の前に確かめる

- [ ] `https://mtg.example.com/` が開き、証明書が有効（HTTP は HTTPS に転送される）
- [ ] 利用規約・プライバシーの下書きを読んで直した（`mtgtable/web/legal.js`。直したら「下書き」の表示を外す）
- [ ] 運営者と連絡先を設定した
- [ ] AI の上限（サイトと、提供元の管理画面の両方）を決めた
- [ ] 別の端末で: デッキ登録 → AI と対局 → 招待して対局 → 履歴から再生 → 共有 URL、が通る
- [ ] `/healthz` を外の監視（UptimeRobot など）で 5 分ごとに見る。503（空きが `MTGTABLE_MIN_FREE_MB` 未満）か応答なしで知らせる
- [ ] バックアップが動いた（`systemctl list-timers mtgtable-backup.timer`、`/var/lib/mtgtable/backups/`）

## 毎日の運用

| こと | どうする |
|---|---|
| ログ | `journalctl -u mtgtable -f`（アクセス・ログと、思いがけない失敗の理由）。Caddy は `/var/log/caddy/mtgtable.log` |
| 監視 | `/healthz`（`free_bytes`・`db_bytes`・回している AI の対局数） |
| AI の利用 | DB の表 `ai_usage`（`sqlite3 mtg.sqlite "select day, sum(tokens) from ai_usage group by day"`） |
| 中断した対局 | 画面に理由が出て、席を持つ人が「再開する」を押せる。API の失敗が続くならキー・提供元の状況を見る |
| バックアップ | 毎日 `backups/` に写る。別の場所（オブジェクト・ストレージなど）にも週1で写しておく |
| 作業ファイル | `/var/lib/mtgtable/mtg-files/`（AI のプロンプトと返答、再生の時系列）。消しても対局は壊れない（時系列は作り直す） |

## 更新する

```bash
cd /opt/mtgtable && sudo -u mtgtable git pull   # （root で clone したなら sudo git pull）
/opt/mtgtable/.venv/bin/python -m unittest discover -s tests
sudo systemctl restart mtgtable
```

- DB の表は起動時に足りないものだけ作る（今ある表は変えない）
- 動かしている AI の手番の途中で止めても、次に起きたときに続きから回る

## 戻す（バックアップから）

```bash
sudo systemctl stop mtgtable
sudo -u mtgtable cp /var/lib/mtgtable/backups/mtg-YYYYMMDD-HHMMSS.sqlite /var/lib/mtgtable/mtg.sqlite
sudo rm -f /var/lib/mtgtable/mtg.sqlite-wal /var/lib/mtgtable/mtg.sqlite-shm
sudo systemctl start mtgtable
```
