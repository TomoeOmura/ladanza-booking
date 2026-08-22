# La Danza Flask予約システム

Googleカレンダーを空き状況の基準にするFlask版です。営業時間は10:00〜22:00、予約開始時刻は30分単位、日曜は表示しません。メニューの所要時間（20・30・60分）全体が空いている場合だけ予約できます。

## 1. Google Cloudの準備

1. [Google Cloud Console](https://console.cloud.google.com/)でプロジェクトを作成します。
2. 「APIとサービス」→「ライブラリ」で **Google Calendar API** を検索し、「有効にする」を押します。
3. 「IAMと管理」→「サービス アカウント」→「サービス アカウントを作成」を押します。名前は `ladanza-booking` などで構いません。通常、この用途ではプロジェクト自体への強いIAMロールは不要です。
4. 作成したサービスアカウントを開き、「キー」→「鍵を追加」→「新しい鍵を作成」→「JSON」→「作成」を押します。
5. ダウンロードしたJSONを `credentials.json` に改名します。このファイルは秘密鍵です。GitHubやメールへ公開しないでください。

## 2. Googleカレンダーを共有する

1. `credentials.json` をテキストエディターで開き、`client_email` の値（例: `ladanza-booking@project.iam.gserviceaccount.com`）を控えます。
2. 予約を書き込みたいGoogleカレンダーを開き、「設定と共有」→「特定のユーザーまたはグループと共有する」へ進みます。
3. 先ほどの `client_email` を追加し、権限を **予定の変更** にします。
4. 同じカレンダー設定の「カレンダーの統合」から **カレンダーID** をコピーします。講師別カレンダーがある場合は、各カレンダーで同じ共有操作を行います。

## 3. ローカル起動

このフォルダー直下に `credentials.json` を置きます。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

`.env` のカレンダーIDを実際の値へ変更してから、`http://localhost:8000` を開きます。

```env
GOOGLE_CALENDAR_ID=共通または既定のカレンダーID
GROUP_CALENDAR_ID=グループレッスン用カレンダーID
CALENDAR_MAP_JSON={"大村 尊":"講師1のID","大村 友恵":"講師2のID","廣瀬 裕貴":"講師3のID"}
ADMIN_TOKEN=admin
```

初期パスワードは `admin` です。Renderへ公開する前に、`ADMIN_TOKEN` を十分に長い推測されにくい値へ必ず変更してください。

起動後、`http://localhost:8000/admin` を開き、`ADMIN_TOKEN` に設定したパスワードでログインします。「大村 尊」「大村 友恵」「廣瀬 裕貴」「スタジオ主催」を色分けしたタブから選び、先生ごとに10:00〜21:30の30分枠を曜日別に設定できます。「日付を指定」へ切り替えると、特定の日だけ通常の曜日設定を変更できます。「曜日設定をコピー」「この日を休み」「個別設定を解除」に対応し、個別設定がない日は毎週の曜日設定を自動使用します。60分レッスンでは、その講師について連続する2つの30分枠が両方有効な場合だけ予約できます。

## 4. Renderへデプロイ

1. `credentials.json` と `.env` を除くファイルをGitHubリポジトリへpushします。`.gitignore` が両方を除外します。
2. Renderで **New → Web Service** を選び、GitHubリポジトリを接続します。`render.yaml` を使う場合はBlueprintから作成できます。
3. Build Commandは `pip install -r requirements.txt`、Start Commandは `gunicorn main:app` です。
4. Renderのサービス画面で **Environment → Secret Files → Add Secret File** を選びます。
5. Filenameを `credentials.json` とし、Googleから取得したJSON全文を内容欄へ貼り付けます。コードは `/etc/secrets/credentials.json` から読み込みます。
6. Environment Variablesへ `GOOGLE_CALENDAR_ID`、`GROUP_CALENDAR_ID`、`CALENDAR_MAP_JSON`、公開後の `PUBLIC_URL`、管理画面用の `ADMIN_TOKEN` を設定します。
7. 保存して再デプロイし、`https://あなたのURL/health` が `{"ok":true}` を返すことを確認します。

## API

- `GET /api/slots?instructor=大村%20尊&menu=個人レッスン%2060分` — Google予定を除いた14日分の空き枠
- `POST /api/bookings` — 空き状況を再確認して予約・Google予定登録
- `GET /api/reservations/<token>` — 予約内容確認
- `POST /api/reservations/<token>/cancel` — Google予定削除とキャンセル

## 運用上の注意

- サービスアカウント鍵は絶対にGitへコミットしないでください。漏えいした場合はGoogle Cloud Consoleで直ちに鍵を無効化・削除します。
- Renderの通常ファイルシステムは永続保存用途には向きません。本番で予約確認データを確実に保持するには、Render Persistent Diskを付けて `DATABASE_PATH` をその配下へ設定するか、PostgreSQLへ移行してください。空き枠判定自体はGoogleカレンダーを参照します。
- 同時予約が非常に多い場合はGoogle予定作成前後の排他制御を追加してください。本実装は小規模スタジオ向けです。
