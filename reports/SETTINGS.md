# Pyramide 설정값 정리

마지막 갱신: 2026-11-22 · 새 설정이 생기거나 값이 바뀌면 이 문서(reports/SETTINGS.md)와 PDF(reports/SETTINGS.pdf)를 같이 갱신한다.

## 바꾸는 법

1. 해당 파일(주로 `trading_server.py`)을 메모장 등으로 열어 `이름 = 값` 줄을 찾아 값만 바꾼다.
2. 저장하면 PC 수집기는 몇 분 안에 자동 재시작되고 GitHub에도 올라간다.
3. **서버(trading_server.py)는 직접 재시작**해야 반영된다. 클라이언트 설정은 클라이언트 재시작.
4. 다른 기기는 `git pull` 후 서버/클라 재시작.
5. 되돌리고 싶으면 `backups\날짜_이름\` 폴더의 원본 파일로 덮어쓰면 된다.

## 1. 판정 (trading_server.py)

카드의 "✅ 판정 롱/숏"을 정하는 규칙. 판정 v2(10/15~)부터는 누적 점수식이 아니라 확률모델(p_up, exp4) 기준이다.

| 설정 | 현재값 | 뜻 | 끄기/되돌리기 |
|---|---|---|---|
| `VERDICT_V2` | True | 판정 v2 사용. 롱 = 기존 롱 후보 ∩ p_up≥0.50 ∩ 4h예상>+0.2%, 숏 = 4h예상<-0.5% ∩ p_up<0.45(모델만) | False면 예전 점수식 판정 |
| `V2_LONG_EXP4_MIN` | 0.2 | 롱 판정에 필요한 최소 4h 예상수익(%) | 높이면 롱 신호 줄고 정확도↑ |
| `V2_SHORT_EXP4_MAX` | -0.5 | 숏 판정 기준 4h 예상수익(%) 상한 | 더 낮추면(-0.7) 숏 신호 줄고 승률↑ |
| `V2_SHORT_PUP_MAX` | 0.45 | 숏 판정에 필요한 24h 상승확률 상한 | |
| `V2_SHORT_BLOCK_MKT_R4` | 1.0 | 40종목 평균 4h 등락이 이 % 이상(반등 중)이면 모델 숏 대기 | 999로 하면 끔 |
| `USE_SHORT_STOP_BLOCK` | True | 숏 손절 후 차단. 숏 판정이 뜬 뒤 그 코인이 판정 시점 가격보다 일정 % 이상 오르면 그 코인 숏 판정을 일정 시간 막음(같은 코인 반복 손실 방지). 카드엔 "숏 손절차단 ~시각"으로 표시 | False면 끔 |
| `SHORT_STOP_PCT` / `SHORT_STOP_WATCH_H` / `SHORT_STOP_BLOCK_H` | 3.0 / 24 / 24 | 판정 후 24h 안에 +3% 역행하면 24h 차단. 1년 검증: 12h 승률 62→65%, 10% 이상 역행 9.4→6.6% | 서버·수집기 재시작하면 차단 기록 초기화 |
| `SHORT_REQUIRE_FUTURES` | True (⚠ 11/21 21시 덮어쓰기로 서버 코드에서 빠져 있음 — 복구 전까지 미작동) | 숏 판정은 바이낸스 선물 있는 코인만. 선물이 없는 코인(카드에 '선물 미상장', NOFUT)은 숏 판정 대신 "숏후보 바이낸스 선물 없음 대기". 롱 판정은 그대로. 바이낸스 롱숏비율이 안 잡히면 NOFUT로 보므로, 바이낸스 응답이 잠깐 끊기면 그동안 숏이 대기될 수 있음 | False면 예전처럼 모든 코인 숏 |
| `DROP_NO_PRICE_ON_CLOSE` | True | 청산을 눌렀는데(자동 정리 포함) 가격을 끝내 못 구하면(약 4초 재시도) 포지션을 지움. 증거금은 잔고로 그대로 돌려주고(손익 0), 그 포지션의 진입 기록은 지우고 청산 기록도 안 남김. 서버 로그에 🗑️로 표시 | False면 예전처럼 "가격 없음" 실패 |
| `BEAR_LONG_BLOCK` | True | 레짐이 하락장이면 롱 판정 대기 | False면 하락장에도 롱 |
| `BEAR_LONG_UNLOCK_MKT_R24` | 0.0 | 하락장이라도 40종목 평균 24h 등락이 이 %보다 크면 롱 허용 | 999로 하면 해제 기능 끔 |
| `USE_PROB_FILTER` | True | 24h 상승확률 필터(롱 p_up≥0.50, 숏 p_up≤0.45) | False면 필터 없이(표시는 유지) |
| `PROB_LONG_MIN` | 0.55 | 롱 판정 최소 상승확률 (10/30 0.55→0.50 → 11/12 0.55 복귀: 0.50 이후 p_up 0.50~0.55 롱 12h 승률 33%(6건) vs ≥0.55 80%(5건)) | 0.50으로 내리면 롱 판정이 늘어남(10/30~11/11 상태) |
| `PROB_SHORT_MAX` | 0.45 | (v2 꺼졌을 때) 숏 판정 최대 상승확률 | |
| `VERDICT_CUT` | 50 | (판정 후보) 롱/숏 점수 컷 | |
| `VERDICT_PP_CUT` | 80 | (판정 후보) 매집/분산 점수 컷 | |
| `VERDICT_LONG_COND` / `VERDICT_SHORT_COND` | 조건식 롱 v7 / 숏 v3 | (판정 후보) 매집·분산과 같이 봐야 하는 조건식. 클라 색칠용 같은 내용: `long_conditions_import_v7.txt`, `short_conditions_import_v3.txt`. 10/29 클라 색칠용 롱 v8(`long_conditions_import_v8.txt`, v7 각 절에 BB<20 추가)도 생김 — 서버 판정은 v7 그대로 | 예전 식은 v5 / v1+v2 파일에 그대로 있음. v8은 클라 '가져오기'로 넣어야 적용 |

**검증치(2년 워크포워드, 수수료 미반영)**: 롱 4h 승률 59%·12h 57%, 숏 4h 66%·12h 69%·24h 71%. 시장 반등 보정 후 숏 4h 68%. 숏을 10배로 잡으면 24h 내 청산선 도달 약 22% → 권장 레버리지 필수.

## 2. 점수 보정 (trading_server.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `LONG_LOW_ATR_THRESHOLD` / `LONG_LOW_ATR_FACTOR` | 1.2 / 0.5 | ATR 1.2% 미만 코인은 롱 점수 ×0.5 (10/11 자동 적용) |
| `PREPUMP_SCORE_VERSION` | 7 | 매집/분산 점수 버전(7 = 10/12 개편판) |
| `USE_RUNUP_SHORT_BONUS` | False | 3일 급등 숏 보너스(홀드아웃 실패로 꺼둠) |
| `USE_SHORT_RISK_CAP` / `SHORT_RISK_CAP_SCORE` | True / 49 | 청산위험(ATR≥2% 또는 EMA20+4% 이상) 종목은 숏점수 최대 49 (10/17 자동 적용) |
| `REGIME_CONFIRM_SECONDS` | 900 | 레짐(상승/하락/횡보/고변동)이 15분 유지돼야 전환 확정 |
| `USE_LOGISTIC_SCORE_MODEL` / `USE_SCORE_CALIBRATION` / `USE_CONDITION_MATCH_BONUS` | False | 예전 실험 기능(꺼짐 유지) |

## 3. 청산 위험 / 권장 레버리지 (trading_server.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `LIQ_LEV_ATR_MULT` | 5 | 청산선이 ATR의 5배 밖에 오도록 권장 레버리지 계산(`권장≤Nx`) |
| `LIQ_LEV_MAX` | 10 | 권장 레버리지 상한. 바이낸스 선물 미상장 코인은 절반 |
| `SHORT_RISK_ATR` | 2.0 | ATR% 이 이상이면 숏 청산위험 플래그(🔥 뱃지) |
| `SHORT_RISK_EMA_DIST` | 4.0 | 가격이 EMA20보다 이 % 이상 위면 숏 청산위험 플래그 |

## 4. 확률모델 (prob_model.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `RETRAIN_HOURS` | 20 | 이보다 오래된 모델이면 수집기가 자동 재학습 |
| `MODEL_VERSION` | 2 | 2 = 24h 상승확률(p_up) + 4h 시장대비 예상수익(exp4) |

모델 파일: `models\prob_model.pkl`(PC 전용) + `models\prob_model_lite.json`(GitHub로 배포, scikit-learn 없는 기기용). 학습 로그는 `models\train_log.txt`.

## 5. 수집기 (data_collector.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `COLLECT_INTERVAL_SEC` | 300 | 수집 주기(초) |
| `AUTO_PUSH_INTERVAL_MIN` | 30 | GitHub 자동 push 주기(분) |
| `MODEL_CHECK_INTERVAL_MIN` | 60 | 과거 캔들 다운로드·모델 재학습 필요 여부 확인 주기(분) |
| `WATCHDOG_MIN` | 20 | 이 시간(분) 넘게 로그 저장이 없으면 수집기가 스스로 재시작(멈춤 자동 복구). run_collector.bat로 켜면 bat가 다시 띄우고, 직접 켜면 새 창으로 다시 뜸 |

## 6. 클라이언트 버튼 설정 (기기별)

| 버튼 | 저장 파일 | 뜻 |
|---|---|---|
| 확률필터 ON/OFF | `prob_filter.json` | OFF면 필터 적용 전 판정을 표시(서버 판정·로그는 그대로) |
| 자동진입 (설정 창) | `auto_entry.json`, 기록 `auto_entry_log.csv` | 1회 진입은 **고정 $** 또는 **현금의 %**(`amt_mode`=usd/pct, `AUTO_ENTRY_PCT`=3, 진입할 때마다 그 시점 현금 기준 — 11/22). 켜면 판정 롱/숏이 새로 뜰 때 증거금·레버리지(기본 $30×3, 하락장이면 롱 자동진입 안 함 체크 기본 켜짐 — `skip_bear_long`, 강한 숏(4h예상 -1% 이하)은 진입 금액 2배 기본 켜짐 — `strong_x2`, `AUTO_ENTRY_STRONG_SHORT_EXP4`=-1.0·`AUTO_ENTRY_STRONG_MULT`=2.0)로 바로 진입. 잔고 제한=충전 잔고 안에서만(부족하면 건너뜀), 잔고 무시=서버 `open_force`로 강제 진입(잔고 마이너스 가능). 클라이언트 켜져 있을 때만. 바이낸스 선물에 없는 코인(NOFUT)은 롱·숏 모두 자동진입 안 함(롱 판정 표시는 그대로). "진입 후 N시간 자동 정리"(기본 48, 0=안 함)는 자동진입 포지션만 정리(`auto_entry_positions.json`), 클라가 꺼져 있던 동안 시간이 지났으면 켜질 때 정리 |
| 순위 탭 | – | 4h 예상수익(exp4) 순 롱/숏 상위 10. 서버가 구버전이면 p_up 순 |

## 7. 변경 이력(설정 관련)

| 날짜 | 설정 | 백업 폴더 |
|---|---|---|
| 10/11 | LIQ_LEV_*, SHORT_RISK_* 추가 | backups\20261011_liq |
| 10/11 | LONG_LOW_ATR 1.2/0.5 (자동) | backups\20261011 |
| 10/12 | PREPUMP_SCORE_VERSION=7, REGIME_CONFIRM_SECONDS=900 | backups\20261012_v7, 20261012_regime |
| 10/13 | USE_PROB_FILTER, PROB_LONG_MIN/SHORT_MAX | backups\20261013_prob |
| 10/15 | BEAR_LONG_BLOCK | backups\20261015_bear |
| 10/15 | VERDICT_V2, V2_* | backups\20261015_v2 |
| 10/16 | V2_SHORT_BLOCK_MKT_R4, BEAR_LONG_UNLOCK_MKT_R24 | backups\20261016_rebound |
| 10/17 | USE_SHORT_RISK_CAP, SHORT_RISK_CAP_SCORE (자동) | backups\20261017 |
| 10/17 | VERDICT_LONG_COND v7, VERDICT_SHORT_COND v3 (승률 저조 절 제거) | backups\20261017_cond |
| 10/21 | USE_SHORT_STOP_BLOCK, SHORT_STOP_* (숏 손절 후 차단) | backups\20261021_shortstop |
| 10/27 | WATCHDOG_MIN (수집기 멈춤 자동 재시작, data_collector.py·run_collector.bat) | backups\20261027_watchdog |
| 10/29 | 롱 조건식 v8 파일 추가(클라 색칠용, 자동). VERDICT_LONG_COND는 변경 없음 | 새 파일이라 원본 없음(SETTINGS 백업: backups\20261029) |
| 10/30 | PROB_LONG_MIN 0.55→0.50 | backups\20261030_problong |
| 11/12 | PROB_LONG_MIN 0.50→0.55 (복귀, 사용자 승인) | backups\20261112_problong |
| 11/21 | 클라 자동진입 버튼(잔고 제한/무시, N시간 자동 정리), 서버 open_force 명령 추가 | backups\20261121_autoentry |
| 11/21 | SHORT_REQUIRE_FUTURES=True(숏 판정 바이낸스 선물 코인만), 자동진입은 바이낸스 선물 없는 코인 롱·숏 모두 건너뜀 (사용자 요청) | backups\20261121_binance_short |
| 11/21 | 자동진입·live_trader.py 강한 숏(4h예상 ≤-1%) 진입 금액 2배 기본 켜짐(1년 백테스트 +$887→+$1,324) | backups\20261121_strong_short |
| 11/21 | 자동진입 기본값 $30·3배·48h 정리·하락장 롱 제외(사용자 요청, 1년 백테스트 3배 48h $30: +$887·청산 16·최악월 -$20) | backups\20261121_autoentry_defaults |
| 11/21 | 자동진입 기본값 레버리지 10→5배, 자동 정리 12→24h (trading_client.py AUTO_ENTRY_LEVERAGE/AUTO_ENTRY_HOLD_H, 이미 저장된 auto_entry.json 설정은 그대로) | backups\20261121_autoentry_24h5x |
| 11/22 | 클라 자동진입 복구(11/21 21시 덮어쓰기로 빠졌던 것): 바이낸스 선물 없는 코인 롱·숏 건너뜀, `AUTO_ENTRY_USD/LEVERAGE/HOLD_H` 200·10·12 → 30·3·48, `AUTO_ENTRY_SKIP_BEAR_LONG`=True, `AUTO_ENTRY_STRONG_SHORT_EXP4`=-1.0·`AUTO_ENTRY_STRONG_MULT`=2.0 (사용자 요청). 서버 SHORT_REQUIRE_FUTURES는 아직 미복구 | backups\20261122_binance_auto (바이낸스만 넣기 전), backups\20261122_autoentry_restore (기본값·2개 규칙 넣기 전) |
| 11/22 | DROP_NO_PRICE_ON_CLOSE=True (가격 없어 청산 안 되는 포지션은 기록째 삭제·증거금은 잔고로 환불, 사용자 요청, trading_server.py srv_close) | backups\20261122_drop_noprice |
| 11/22 | Claude 보고용 사본 `claude_view/`(서버·클라·live_trader, 1분마다, `CLAUDE_VIEW`), 1회 진입 '현금의 %' 모드(클라 자동진입 `amt_mode/pct`, live_trader `margin_pct`·모의 가상현금 `DRY_SEED_USDT`=1000) (사용자 요청) | backups\20261122_claude_view_pct |
