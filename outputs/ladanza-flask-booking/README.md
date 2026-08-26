# La Danza Flask予約システム

La Danza Dance Studioの予約画面・講師別予約枠管理・Googleカレンダー連携を提供するFlaskアプリです。

## 予約ルール

- 講師：大村 尊、大村 友恵、廣瀬 裕貴
- 管理画面ではサロンとチャーターの予約枠を別々に設定します。お客様は担当講師を選択しません。
- 通常メニュー：個人レッスン60分・30分、初心者パック30分、初級パック30分、サロン、チャーター30分
- LINE友だち追加専用メニュー：無料体験20分（電話番号ごとに1回）
- 3名の講師は、同じ時刻に別々の予約を受けられます。
- 同じ講師の時間が重なる予約は受け付けません。
- サロンは同じ回に10名、チャーターは同じ回に6名まで受け付けます。
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

更新日：2026年8月26日
