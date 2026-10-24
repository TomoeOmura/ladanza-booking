# La Danza Flask予約システム

La Danza Dance Studioの予約画面・講師別予約枠管理・Googleカレンダー連携を提供するFlaskアプリです。

## 予約ルール

- 講師：大村 尊、大村 友恵、廣瀬 裕貴
- 管理画面ではサロン、チャーター、テーマパークダンスの予約枠を別々に設定します。毎週の曜日・時間と日付指定の両方に対応し、お客様は担当講師を選択しません。
- 通常メニュー：個人レッスン60分・30分、初心者パック30分、初級パック30分、サロン、チャーター30分、テーマパークダンス60分
- LINE友だち追加専用メニュー：無料体験20分（電話番号ごとに1回）
- 3名の講師は、同じ時刻に別々の予約を受けられます。
- 同じ講師の時間が重なる予約は受け付けません。
- サロンは同じ回に10名、チャーターは同じ回に6名、テーマパークダンスは同じ回に15名まで受け付けます。
- テーマパークダンスは管理画面で開始時刻を1個選ぶと、その時刻から60分の予約枠になります。
- GoogleカレンダーはLa Danza共通の1個を使用します。アプリが作成する予定には担当講師情報を付け、講師別に重複を判定します。
- 講師情報が付いていないGoogleカレンダー予定は、スタジオ全体の休業・使用不可時間として全講師を停止します。

## 安全上の変更

- API接続に失敗した場合、架空の予約枠は表示しません。
- 管理パスワードに既定値はありません。
- 予約確認用tokenと二重送信防止キーは、Googleカレンダーの非公開項目へハッシュ化して保存します。
- 同意日時と予約元（`website` / `line`）を保存します。
- 空き状況・グループ残席・予約確認・キャンセルはGoogleカレンダーを基準に処理します。
- 受付時間などの管理設定もGoogleカレンダーの非公開項目へ圧縮して保存します。
- Google認証JSONはGitHubへ保存しません。

## 必須の環境変数

```env
GOOGLE_CREDENTIALS_FILE=/etc/secrets/credentials.json
GOOGLE_CALENDAR_ID=La Danza共通カレンダーID
PUBLIC_URL=https://公開するRender URL
ADMIN_TOKEN=十分に長い管理パスワード
LINE_CHANNEL_ACCESS_TOKEN=LINE Messaging APIのチャネルアクセストークン
LINE_ADMIN_USER_ID=通知先となる管理者のLINEユーザーID
```

`ADMIN_TOKEN`、LINEのチャネルアクセストークン、管理者ユーザーIDは、チャット、GitHub、メールへ貼り付けないでください。

## Google Cloud / Googleカレンダー

1. Google Calendar APIを有効にします。
2. 予約専用サービスアカウントの新しいJSON鍵を作成します。
3. La Danza共通カレンダーをサービスアカウントへ「予定の変更」権限で共有します。
4. RenderのSecret Fileとして `/etc/secrets/credentials.json` に設定します。
5. 漏えいした可能性のある旧鍵をGoogle Cloud Consoleで無効化・削除します。

## Render

- Root Directory：`outputs/ladanza-flask-booking`
- Build Command：`pip install -r requirements.txt`
- Start Command：`gunicorn main:app`
- Health Check Path：`/health`

Renderの対象サービスで「Environment」を開き、`LINE_CHANNEL_ACCESS_TOKEN`と
`LINE_ADMIN_USER_ID`をSecretとして追加します。Messaging APIチャネルにはプッシュ
メッセージを送信できるチャネルアクセストークンを設定し、通知先には管理者本人の
ユーザーIDを設定してください。どちらかが未設定の場合は通知を省略しますが、予約と
Googleカレンダー登録は通常どおり完了します。

予約情報と管理設定はGoogleカレンダーへ保存するため、Renderの永続ディスクと有料インスタンスは不要です。無料インスタンスは15分間アクセスがないと休止し、次回の初回表示に時間がかかる場合があります。

## 導線

- ホームページ：`https://公開URL/?source=website`
- 公式LINE：`https://公開URL/?source=line`
- 公式LINEの予約確認：`https://公開URL/reservation-lookup`
- 管理画面：`https://公開URL/admin`

## 日付別の予約確認（2026年10月24日追加）

- 管理画面にログインすると「当日の予約」を表示します。前日・翌日・今日・日付入力で切り替え、講師やメニューで絞り込めます。
- Googleカレンダーを直接読み取り、お客様名、時間、メニュー、担当講師、予約番号を表示します。カレンダーに直接追加した予定も区別して表示します。
- キャンセル済み予約は件数を表示し、「キャンセルも表示」で一覧に含められます。日時は日本時間です。
- 「再読み込み」で最新の状況を確認できます。取得に失敗した場合はエラーを表示し、予約が0件とは表示しません。
- 一覧は閲覧専用です。「予約枠の設定」から既存の受付時間設定へ切り替えられます。
- `GET /api/admin/bookings?date=YYYY-MM-DD` は既存の管理認証が必要です。レスポンスは保存・キャッシュを禁止し、認証用の情報や連絡先を返しません。

## チャーターの開催日（2026年10月24日修正）

- チャーターは毎月第1木曜日のみです。時間帯は既存の木曜日設定を使用します。
- 第1木曜日の休講・時間変更は日付指定で設定できます。第1木曜日以外は日付指定があっても受付不可です。
- 公開空き枠と予約確定APIの両方で開催日を検証します。既存の予約は自動キャンセルしません。

## ローカル確認

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

## API

- `GET /api/slots?instructor=大村%20尊&menu=個人レッスン%2060分`
- `POST /api/bookings`
- `GET /api/reservations/<token>`
- `POST /api/reservations/<token>/cancel`
- `POST /api/reservations/lookup`
- `POST /api/reservations/lookup/cancel`
- `GET /api/admin/settings`
- `PUT /api/admin/settings`

更新日：2026年9月1日


## 社会人サークル（2026年10月14日追加）

- 第2・第4水曜日19:30〜20:30、定員10名。初回1,000円／通常1,500円を予約画面に表示します。料金の自動決済は行いません。
- 2026年10月14日以降の開催日を自動計算します。第1・第3・第5水曜日には通常枠を出しません。
- 管理画面の「社会人サークル」で開始時刻と日付指定を管理できます。日付指定は通常開催日より優先され、休講（空の枠）・振替を設定できます。
- 既存設定に社会人サークルがなくても、新しいメニューと通常枠のみ補完します。他の予約枠は保持します。

### 管理画面の月カレンダー・時間帯入力（2026-10-24）

- 「予約カレンダー」に月の予約件数・カレンダー予定件数・空き状況を表示。日付を選ぶと予約一覧と講師別の空き時間を確認できます。空き時間は公開予約と同じ競合・定員の判定を使用します。個人レッスンは30分メニュー基準です。
- 予約枠は開始〜終了の時間帯で入力。既存の30分枠と日付別設定をそのまま読み書きします。社会人サークルとテーマパークダンスは従来どおり開始時刻を保存し、終了はメニューの所要時間から表示します。
- 「繰り返し」の曜日ごとに毎週／毎月第1〜第5／第1・第3／第2・第4を設定できます。`recurrence_rules[resource][weekday]` は1〜5の週番号配列で、未指定は毎週。日付別設定が優先します。チャーターの第1木曜制限とサークルの第2・第4水曜の既存ルールは維持します。
- 保存前に変更前後を確認。失敗時は入力を保持します。設定GETの `revision` をPUTに添え、他画面で更新された場合は409で上書きを防ぎます。旧画面からの保存では新しい繰り返し設定を保持します。
- 管理API: `GET /api/admin/month?month=YYYY-MM`、`GET /api/admin/bookings?date=YYYY-MM-DD`。管理トークンが必須でキャッシュ不可。月APIは顧客氏名を返しません。

検証: `python -m unittest -q`。画面操作テストは開発用に `jsdom` をインストールした環境で `node tests/admin-ui.cjs`（本番依存には追加不要）。
