# アクチュエータ秒数制御 設計メモ

> Status: 未実装・設計検討中
> Date: 2026-02-19

## 背景

CCMアクチュエータにはフィードバック（現在開度）がない。
モーター通電時間で開度を制御する必要がある。

## アクチュエータ分類

### 秒数制御型（ON時間 = 動作量）

| 種別 | CCM Type | 物理リミット | 備考 |
|------|----------|-------------|------|
| 電磁弁 | Irri | なし | 時間 = 流量。開けっぱなし注意 |
| 天窓 | VenRfWin | あり（機械式上下限） | 全開/全閉でモーターが止まる |
| 側窓 | VenSdWin | あり（機械式上下限） | 同上 |
| 保温カーテン | ThCrtn | あり（機械式上下限） | 同上 |
| 遮光カーテン | LsCrtn | あり（機械式上下限） | 同上 |

### ON/OFF型（秒数制御不要）

| 種別 | CCM Type | 備考 |
|------|----------|------|
| 換気扇 | VenFan | 単純ON/OFF |
| 攪拌扇 | CirHoriFan | 単純ON/OFF |
| 暖房バーナー | AirHeatBurn | 単純ON/OFF |
| 暖房HP | AirHeatHP | 単純ON/OFF |
| CO2発生器 | CO2Burn | 単純ON/OFF |
| 冷房HP | AirCoolHP | 単純ON/OFF |
| 加湿フォグ | AirHumFog | 単純ON/OFF |

## 状態管理

### 必要な状態

```json
{
  "VenSdWin": {
    "position_pct": 0,
    "full_travel_sec": 60,
    "last_calibrated": "2026-02-19T10:00:00Z"
  }
}
```

### 保持場所

- **RPi側ブリッジ (state.json)** が最適
  - 常駐しているので状態が消えにくい
  - 実機に最も近い
  - PC側VPN切断でも状態を保持

## 動作シーケンス

### 「側窓30%開けて」の場合

```
1. 現在: position_pct = 0%
2. 目標: 30%
3. 必要動作: ON送信 → full_travel_sec * 0.30 秒待機 → OFF送信
4. 完了後: position_pct = 30% に更新
```

## 5階層優先度モデル（採用方針）

CCMプロトコルの `priority` (1-30) を活用し、制御命令を5階層に分類する。
上位レベルは下位をプリエンプト（即座に中断）できる。

```
┌─────────────────────────────────────────────────────────┐
│ Level 1: 緊急停止     priority=1    即時割込み           │
│   - 全アクチュエータ即時OFF                              │
│   - クーリング無視、ロック無視                           │
│   - トリガー: 人間の緊急指示、安全センサー異常           │
├─────────────────────────────────────────────────────────┤
│ Level 2: 安全制御     priority=5    自動安全ルール        │
│   - 凍結防止（気温低下→カーテン閉）                      │
│   - 過熱防止（高温→天窓開）                              │
│   - 強風保護（風速超過→天窓閉）                          │
│   - 同レベル動作中でも割り込み可                         │
├─────────────────────────────────────────────────────────┤
│ Level 3: 手動指示     priority=10   人間/AIの明示的指示   │
│   - 「側窓30%開けて」「灌水5分」                         │
│   - 同レベル動作中はロック（クーリング適用）              │
│   - Level 2以上から割り込まれる                          │
├─────────────────────────────────────────────────────────┤
│ Level 4: 自動制御     priority=20   スケジュール/ルール   │
│   - 定時灌水、日の出連動カーテン開閉                     │
│   - Level 3以上が動作中なら待機                          │
│   - 同レベルはFIFOキュー                                │
├─────────────────────────────────────────────────────────┤
│ Level 5: デフォルト   priority=29   ArSprout自律制御     │
│   - ブリッジからの制御パケットが途絶した場合              │
│   - ArSprout側が自律的に動作する既存の仕組み             │
│   - フォールバック安全ネット                              │
└─────────────────────────────────────────────────────────┘
```

### 割り込みルール

| 実行中 \ 新規 | L1 緊急 | L2 安全 | L3 手動 | L4 自動 |
|--------------|---------|---------|---------|---------|
| L2 安全制御   | 即中断  | 即中断  | 待機    | 待機    |
| L3 手動指示   | 即中断  | 即中断  | ロック  | 待機    |
| L4 自動制御   | 即中断  | 即中断  | 即中断  | ロック  |
| クーリング中  | 即中断  | 即中断  | 拒否    | 拒否    |

- **即中断**: 現在のタイマーをキャンセル、位置を確定し、新命令を実行
- **ロック**: 「動作中です、完了まで待ってください」を返す
- **待機**: キューに入れて現在の動作完了後に実行
- **拒否**: 「クーリング中です、N秒後に再試行してください」を返す

### 状態遷移

```
IDLE → [命令受信] → MOVING → [タイマー完了] → COOLING → [クーリング完了] → IDLE
                       │                          │
                       │ L1/L2割込み               │ L1/L2割込み
                       ▼                          ▼
                    位置確定 → 新命令実行        クーリング中断 → 新命令実行
```

## 設計決定事項

### 1. 同時操作

- 電力制約は当面無視
- 複数アクチュエータの同時操作は許可

### 2. キャリブレーション

- **午前0時に全閉ルールタスクを自動実行** → position=0% リセット
- **ブリッジ起動時にも全閉ルールタスクを実行**
- 全閉/全開の所要時間はユーザーがYAMLで設定（ハウスごとに異なるため）
- キャリブレーション中は L3/L4 をブロック

### 3. クーリング時間

- ArSproutの実装: 動作終了後にクーリング時間を設定
- クーリング中は同レベル以下の操作を受け付けない（モーター保護）
- L1/L2はクーリングを無視して割り込み可能
- パラメータ: `cooling_sec` をアクチュエータごとに設定

### 4. 安全停止（ブリッジ停止時・ネットワーク断）

- Level 5（ArSprout自律制御）にフォールバック
- ブリッジからのパケットが途絶 → ArSprout側が自律制御に戻る
- CCMプロトコルのpriority機構がそのまま活きる
- ネットワーク断（VPN切れ等）: ローカルPC側はStarlink等で冗長化を検討

### 5. 電磁弁 (Irri) の特殊性

- 物理リミットがないため、開けっぱなし = 水が出続ける
- 最大時間ガード（現在3600秒）は必須
- 全レベルで最大時間を強制（L1緊急停止でも最大値チェック）

### 6. 設定ファイル (actuator_config.yaml)

```yaml
# ユーザーがキャリブレーション値を記述
actuators:
  VenSdWin:
    type: duration
    full_open_sec: 60       # 全閉→全開の所要時間（ユーザー計測）
    full_close_sec: 55      # 全開→全閉の所要時間（重力で速い場合あり）
    cooling_sec: 5
  VenRfWin:
    type: duration
    full_open_sec: 45
    full_close_sec: 40
    cooling_sec: 5
  ThCrtn:
    type: duration
    full_open_sec: 90
    full_close_sec: 90
    cooling_sec: 5
  LsCrtn:
    type: duration
    full_open_sec: 90
    full_close_sec: 90
    cooling_sec: 5
  Irri:
    type: duration
    max_duration_sec: 3600
    cooling_sec: 3
    has_limit: false
  VenFan:
    type: onoff
    cooling_sec: 0
  CirHoriFan:
    type: onoff
    cooling_sec: 0
  AirHeatBurn:
    type: onoff
    cooling_sec: 0
  AirHeatHP:
    type: onoff
    cooling_sec: 0
  CO2Burn:
    type: onoff
    cooling_sec: 0
  AirCoolHP:
    type: onoff
    cooling_sec: 0
  AirHumFog:
    type: onoff
    cooling_sec: 0

calibration:
  daily_reset_hour: 0      # 毎日午前0時に全閉キャリブレーション
  on_startup: true          # ブリッジ起動時にも全閉実行
```

### ランタイム状態 (state.json — ブリッジが自動管理)

```json
{
  "VenSdWin": {"position_pct": 0, "state": "idle", "last_calibrated": "2026-02-19T00:00:00Z"},
  "VenRfWin": {"position_pct": 0, "state": "idle", "last_calibrated": "2026-02-19T00:00:00Z"},
  "ThCrtn":   {"position_pct": 0, "state": "idle", "last_calibrated": "2026-02-19T00:00:00Z"},
  "LsCrtn":   {"position_pct": 0, "state": "idle", "last_calibrated": "2026-02-19T00:00:00Z"}
}
```

## 要確認タスク

### TASK-A: oprパケットの内容確認

- nodesレスポンスに `Irriopr`, `VenSdWinopr`, `VenFanopr` 等が見えている
- これがArSproutの**実際の動作状態フィードバック**であれば、位置推定の信頼性が向上する
- 確認方法: 駆動系が動いている状態で `ccm_receive_test.py --filter opr` を実行し、
  値の変化を観察する
- **前提**: 駆動系が動いている必要がある（現在ブレーカーOFF）

### TASK-B: ArSproutの操作ログ確認

- ArSproutが灌水等の操作履歴を内部に記録しているか確認
- ArSprout管理画面（192.168.1.65）にログ閲覧機能があるか
- 記録があればブリッジ側でのログ実装を簡素化できる可能性

## ArSproutの参考実装

- 動作終了後にクーリング時間を設ける
- タイマー動作中は追加操作をブロック
- priority機構で緊急停止は別系統
- 高優先度パケットが途絶 → 自律制御にフォールバック（Level 5相当）
