# La Danza Dance Studio 予約システム

## 起動

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn main:app --reload
```

`http://localhost:8000` が予約画面、`/admin` が管理画面です。初期管理者トークンは `ladanza-demo` です。本番では必ず `ADMIN_TOKEN` を変更してください。

## Studioへの埋め込み

公開後の予約画面URLをStudioのiframeに指定してください。推奨高さは `760px`、幅は `100%` です。

## Google Calendar

Google CloudでCalendar APIを有効化し、サービスアカウントJSONのパスを `GOOGLE_SERVICE_ACCOUNT_FILE` に設定します。対象カレンダーをサービスアカウントへ共有し、講師別IDを環境変数に設定してください。未設定時はカレンダー処理のみ安全にスキップされます。

## 初期予約枠

管理画面から講師、開始日時、時間、定員を指定して追加します。定員1はマンツーマン、最大15はグループ用です。開始2時間前になると自動的に受付終了します。
