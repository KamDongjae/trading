# Pyramide 설정값 정리

마지막 갱신: 2026-11-26 · 새 설정이 생기거나 값이 바뀌면 이 문서(reports/SETTINGS.md)와 PDF(reports/SETTINGS.pdf)를 같이 갱신한다.

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
| `V2_SHORT_RSI_MIN` | 0 (11/26 끔, 예전 50) | (11/25) 모델 숏은 RSI가 이 값 이상일 때만. 미만이면 "숏후보 RSI xx<50 대기"(이미 많이 빠진 코인 숏 안 함). 14일 학습 12h 승률 86→89%, 10배 청산 14.3→12.7%, 가상 10배·12h +286→+386%(청산 1→0) | 0이면 끔 |
| `V2_SHORT_BLOCK_MKT_R4` | 1.0 | 40종목 평균 4h 등락이 이 % 이상(반등 중)이면 모델 숏 대기 | 999로 하면 끔 |
| `USE_SHORT_STOP_BLOCK` | True | 숏 손절 후 차단. 숏 판정이 뜬 뒤 그 코인이 판정 시점 가격보다 일정 % 이상 오르면 그 코인 숏 판정을 일정 시간 막음(같은 코인 반복 손실 방지). 카드엔 "숏 손절차단 ~시각"으로 표시 | False면 끔 |
| `SHORT_STOP_PCT` / `SHORT_STOP_WATCH_H` / `SHORT_STOP_BLOCK_H` | 3.0 / 24 / 24 | 판정 후 24h 안에 +3% 역행하면 24h 차단. 1년 검증: 12h 승률 62→65%, 10% 이상 역행 9.4→6.6% | 서버·수집기 재시작하면 차단 기록 초기화 |
| `SHORT_REQUIRE_FUTURES` | True (⚠ 11/21 21시 덮어쓰기로 서버 코드에서 빠져 있음 — 복구 전까지 미작동) | 숏 판정은 바이낸스 선물 있는 코인만. 선물이 없는 코인(카드에 '선물 미상장', NOFUT)은 숏 판정 대신 "숏후보 바이낸스 선물 없음 대기". 롱 판정은 그대로. 바이낸스 롱숏비율이 안 잡히면 NOFUT로 보므로, 바이낸스 응답이 잠깐 끊기면 그동안 숏이 대기될 수 있음 | False면 예전처럼 모든 코인 숏 |
| `DROP_NO_PRICE_ON_CLOSE` | True | 청산을 눌렀는데(자동 정리 포함) 가격을 끝내 못 구하면(약 4초 재시도) 포지션을 지움. 증거금은 잔고로 그대로 돌려주고(손익 0), 그 포지션의 진입 기록은 지우고 청산 기록도 안 남김. 서버 로그에 🗑️로 표시 | False면 예전처럼 "가격 없음" 실패 |
| `BEAR_LONG_BLOCK` | True | 레짐이 하락장이면 롱 판정 대기. 레짐은 거래소별 자기 것(빗썸/업비트/바이낸스 각각, 11/23~ — 전엔 업비트도 빗썸 레짐을 썼음). 클라 자동진입 '하락장 롱 제외'도 그 거래소 레짐 기준 | False면 하락장에도 롱 |
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

## 1-1. 바이낸스 탭 (trading_server.py, 11/23 · 11/25 개편 V3)

바이낸스 USDT 무기한 선물 캔들로 채점하는 세 번째 탭. 결과는 `server_market_binance.csv`. 모의 체결·손익은 원래대로 바이낸스 USD가. **11/25부터 V3**: 대상 = 바이낸스 전체를 필터한 뒤 거래대금 상위 300, 확률모델 = 바이낸스 데이터로 학습한 V3.

| 설정 | 현재값 | 뜻 | 끄기/되돌리기 |
|---|---|---|---|
| `BINANCE_ENABLED` | True | 바이낸스 탭 수집·채점 | False면 끔(서버·수집기) |
| `BINANCE_UNIVERSE` | 'v3' | 대상 코인. 'v3' = 매일 09시 필터(밈·스테이블·배수 제외, 상장 `BINANCE_V3_AGE_D`=365일↑, 30일 내 하루 ±`BINANCE_V3_SHOCK_PCT`=30% 이력 없음, 1h ATR(24h 평균) < `BINANCE_V3_UNI_ATR_MAX`=3, 7일 < +`BINANCE_V3_UNI_R7_MAX`=50%) 후 24h 거래대금(08시 1h봉까지) 상위 `BINANCE_TOP_N`. 목록은 `universe_binance.json`(서버·수집기 공유, 하루 한 번 백그라운드 계산 2~4분, 그동안 예전 목록). BTC는 BTC 특성용으로 항상 포함. 'krw' = 원화 거래소 코인 중 바이낸스 선물 있는 것(11/23~11/25) | 'krw'·`BINANCE_TOP_N`=40·`BINANCE_PROB_PROFILE`='krw' 또는 backups\20261125_v3_binance\ |
| `BINANCE_TOP_N` / `BINANCE_V3_REF_N` | 300 / 200 | 대상 개수 / 시장 4h 평균(숏 반등 대기 `V2_SHORT_BLOCK_MKT_R4`) 계산 기준 묶음(REF = 대상 중 상위 200, V3 학습과 같은 정의) | |
| `BINANCE_PROB_PROFILE` | 'binance_v3' | 판정 확률모델. 'binance_v3' = `models\prob_model_binance_v3.pkl`/`_lite.json`(아래 4장). 모델이 없으면 원화 모델로 대신하지 않고 판정 대기. 'krw' = 원화 학습 모델 | 'krw' |
| 판정 가드 `BINANCE_V3_MIN_LISTING_D` / `BINANCE_V3_ATR_MAX` / `BINANCE_V3_R7_MAX` | 730 / 2.5 / 50 | 바이낸스 탭 판정(롱·숏)은 상장 730일↑ · 1h ATR < 2.5% · 7일 < +50%만. 아니면 "상장 N일(730일 미만) 대기" 등. 확률 없으면 "V3 모델 확률 없음 대기". 수동·자동진입·수집기 기록(→ live_trader) 모두 이 판정 기준 | 0/큰 값이면 사실상 끔 |
| `BINANCE_V3_CLOSED_BAR` | True | (11/26) 바이낸스 V3 확률모델 피처를 닫힌 1h봉까지만으로 계산(진행 중인 봉 제외), 숏 손절 차단 기준가도 닫힌 봉 종가 — 백테스트(봉 마감값)와 같게. 바이낸스 V3 탭은 `V2_SHORT_RSI_MIN`(RSI≥50) 규칙 안 씀(백테스트에 없음, 1년 V3 숏 판정의 99.5%가 이미 RSI≥50이라 결과 같음) | False면 진행 중인 봉 포함(예전) |
| `BINANCE_V3_LONG` | True | (11/26 01시 사용자 요청으로 다시 켬) 끄면 바이낸스 V3 롱 판정 대기("바이낸스 V3 롱 끔 대기"). 1년 검증 상한 근사(점수 롱 후보 조건 뺌): 67건 -$68(앞 -$168 / 뒤 +$100), 가드 없으면 188건 -$118 | True면 롱 판정 냄(가드 같이) |
| `BINANCE_SCORE_MIN_CYCLE_SEC` | 180 | 바이낸스 탭 채점 사이클 최소 간격(초). 300코인 캔들 요청 — API 한도 보호 | |
| `BINANCE_OI_TTL` | 1800 | 바이낸스 탭 OI·롱숏비 캐시(초). 심볼별 요청이라 300코인이면 5분 1000회 한도를 넘음(빗썸·업비트 쪽은 `OI_CACHE_TTL`=60 그대로) | |
| `SERVER_RESTART_EXIT_CODE` | 75 | (11/23) `server_cmds`에 `restart` 명령이 오면 저장 후 이 코드로 종료 → run_server.bat가 30초 뒤 새 코드로 다시 켬(Claude가 원격으로 재시작할 때 씀). `shutdown`은 코드 0으로 종료(bat도 끝) | bat 없이 직접 켠 서버는 둘 다 그냥 꺼짐 |

**V3 검증(reports/binance_backtest11~13_20261125.md, 25/11~26/11 1년 워크포워드, 숏 $30·3배·48h·강한숏 $60·청산 코인 7일 차단)**: V3 · 필터 후 1-300위 · 상장 2년↑ · ATR<2.5 → **327건 승률 78% 건당 +8.5% +$1,063**, 앞/뒤 6개월 +$477/+$586, 청산 5.5%(앞 4.7% / 뒤 5.9%), 마이너스 달 0/13, 최대 낙폭 -$114. 비교: 원화 모델 · 원화 코인 28개 +$347 / 원화 모델 · 필터 후 81-200위 +$2,354(3,124건, 최대 낙폭 -$898, 1-80위는 손해). 처음 보는 코인(학습에서 뺀 반쪽) 시험도 앞뒤 모두 +. 상장 180일↑이면 청산 9.8%로 탈락 → 상장 2년↑ 가드. 검증에는 `V2_SHORT_RSI_MIN`(RSI≥50) 규칙이 없었음(서버엔 11/25부터 공통 적용).

**숏 서버 규칙 맞추기(reports/binance_backtest14_20261125.md)**: 기준 327건 +$1,063과 RSI≥50 추가·클라 재신호 6h 가드·가드를 매 시각 적용 모두 같은 결과(327건 +$1,063) → 숏은 백테스트와 같음. 롱(p_up≥0.55·exp4>+0.2·가드) 67건 승률 낮음 -$68 → 끔.

**비용 기준(11/26~)**: 위 V3 검증 수치(+$1,063 등)는 진입 비용(가격 0.7% = 3배 증거금 -2.1%)만 뺀 값. 11/26부터 백테스트 기본 비용은 진입 0.7% + 정리 0.7% = 증거금 -4.2%(bt3 `COST`=0.042), 모의 서버도 정리 때 `EXIT_SLIPPAGE_RATE` 0.7% 적용. 정리 비용 0/0.05/0.35/0.7% 비교는 binance_backtest15.py(run_binance_backtest15.bat) → reports/binance_backtest15_YYYYMMDD.md. 되돌리기: `EXIT_SLIPPAGE_RATE = 0`, `COST = 0.021` 또는 backups\20261126_exit_slippage\.

예전(11/23) 2차 백테스트: 원화 거래소 코인·원화 모델 239건 승률 72% 청산 5.9% +$564.

## 1-2. 감시 대상 40개 — 안정 유니버스 (trading_server.py, 11/23)

빗썸·업비트 탭에서 점수·판정을 내는 40개 코인을 고르는 규칙. 예전엔 업비트 = 24h 거래대금 상위 40(1분마다 새로), 빗썸 = 시세 API 순서대로 앞 40이라 급등·신규·이슈 코인이 계속 들어와 숏이 스퀴즈에 물렸다(ELSA·PIEVERSE 강제청산).

| 설정 | 현재값 | 뜻 | 끄기/되돌리기 |
|---|---|---|---|
| `UNIVERSE_MODE` | "stable" | "stable" = 아래 규칙 / "top24h" = 예전 방식 | "top24h"로 바꾸고 서버 재시작 |
| `UNIVERSE_MIN_AGE_D` | 365 | 상장(그 거래소 일봉 개수) 이 일수 미만이면 후보 제외. 확인 안 되면 제외 | |
| `UNIVERSE_KEEP_RANK` | 60 | 기존 코인은 이 순위 안이면 유지, 빈자리만 위에서 채움(목록이 자주 안 바뀌게) | |
| `UNIVERSE_REFRESH_HHMM` | (9, 5) | 매일 이 시각 이후 1번 갱신. 순위 = 어제까지 30일 일별 거래대금 **중앙값** | |
| `UNIVERSE_SPIKE_X` / `UNIVERSE_MOVE7_PCT` | 5.0 / 40.0 | 24h 거래대금이 30일 중앙값의 5배 초과, 또는 7일 등락 ±40% 초과면 그 코인 판정 대기("이상치") | 999로 하면 끔 |
| `UNIVERSE_EXCLUDE_BINANCE` | True | 바이낸스 USDT 무기한 선물에 있는 코인은 빗썸·업비트 목록에서 뺌(바이낸스 탭이 맡음). 바이낸스가 '1000PEPE'처럼 배수를 붙인 코인도 앞 숫자(1000·1000000·1M)를 떼서 같이 뺌(11/24). 바이낸스 목록 조회 실패면 이전 목록 유지 | False |
| `UNIVERSE_EXCLUDE_STABLE` / `UNIVERSE_STABLES` | True / USDT·USDC·USD1·USDE·USDS·FDUSD·TUSD·DAI·PYUSD·RLUSD·USDP·BUSD | 스테이블코인은 원화 목록에서 뺌(11/24) | False |
| `UNIVERSE_EX_VER` | 2 | 저장된 universe_krw.json이 이 버전보다 낮으면 서버 켜진 직후 다시 계산 | |

- 업비트 유의종목(market_warning·투자주의 플래그)은 후보에서 뺌. 빗썸은 조회되면 같이 뺌(안 되면 그냥 통과).
- 갱신 결과는 데이터 폴더 `universe_krw.json`에 저장 → 재시작해도 유지. 첫 계산 전엔 저장된 목록 → 없으면 학습 코인(history/)으로 시작하고 1~3분 뒤 교체. 서버 로그에 `[유니버스] upbit 40개 갱신 … +추가 / -빠짐`.
- 바이낸스 탭(`BINANCE_UNIVERSE`='v3')은 바이낸스 전체를 따로 필터하므로 이 설정과 무관.

**백테스트(reports/universe_backtest3_20261123.md, 바이낸스 1h봉 25/11~26/11, 원화 모델, 숏 $30·3배·48h)**: 24h 거래대금 상위 40(매시) 청산 20.5%·$+130·뒤 6개월 -$427·최악월 -$749 → 안정 유니버스 **청산 3.6%·$+340**(앞/뒤 +$199/+$141, 최악월 -$144) ← 채택. 60위 유지 빼면 청산 6.3%, 중앙값→합계 6.7%, 상장 1년→180일 7.6%. ATR<3% 추가는 -$188이라 안 붙임. 업비트·빗썸 전체 과거 데이터가 없어 바이낸스로 대신 검증, 유의종목은 검증 못 함.

## 4. 확률모델 (prob_model.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `RETRAIN_HOURS` | 20 | 이보다 오래된 모델이면 수집기가 자동 재학습 |
| `MODEL_VERSION` | 2 | 2 = 24h 상승확률(p_up) + 4h 시장대비 예상수익(exp4) |

모델 파일: `models\prob_model.pkl`(PC 전용) + `models\prob_model_lite.json`(GitHub로 배포, scikit-learn 없는 기기용). 학습 로그는 `models\train_log.txt`.

바이낸스 전용 모델(11/23, 지금은 안 씀): `BINANCE_HISTORY_DIR`=history_binance, `BINANCE_TRAIN_TOP`=100(매시 24h 거래대금 상위 100만 학습), `BINANCE_TRAIN_DAYS`=1100 → `models\prob_model_binance.pkl`/`_lite.json`. `python prob_model.py binance`로 학습.

**바이낸스 V3 모델(11/25~, 바이낸스 탭이 씀)**: `profile='binance_v3'` → `models\prob_model_binance_v3.pkl`/`_lite.json`. 학습 행 = history_binance 1h봉 중 매일 09시 REF(필터 후 거래대금 상위 `V3_REF_N`=200 — 필터는 바이낸스 탭 대상과 같음, 밈 분류는 `history_binance\_subtypes.json`) 코인, `V3_STEP_H`=4시간마다. 피처 `V3_FEATURES` = 코인 자체 특성(`TICKER_FEATS`) + BTC 24h·72h·168h 등락(시장 평균·순위 특성은 대상 묶음에 따라 값이 바뀌어서 뺌). exp4 목표 = 4h 수익 − 같은 시각 REF 평균. 재학습 `V3_RETRAIN_HOURS`=168(1주, 수집기). 직접 학습: `python prob_model.py binance_v3` 또는 train_binance_v3.bat.

## 5. 수집기 (data_collector.py)

| 설정 | 현재값 | 뜻 |
|---|---|---|
| `COLLECT_INTERVAL_SEC` | 300 | 수집 주기(초) |
| `AUTO_PUSH_INTERVAL_MIN` | 30 | GitHub 자동 push 주기(분) |
| `MODEL_CHECK_INTERVAL_MIN` | 60 | 과거 캔들 다운로드·모델 재학습 필요 여부 확인 주기(분) |
| `BINANCE_MODEL_JOBS` | False | (11/23) True면 20시간마다 바이낸스 1시간봉 이어받기 + 바이낸스 전용 모델 재학습. 바이낸스 탭 판정은 매 주기 `market_data_log_binance_YYYYMM.csv`·`signal_outcomes_verdict_binance.csv`에 기록(항상) |
| `BINANCE_V3_MODEL_JOBS` | True | (11/25) 서버 `BINANCE_PROB_PROFILE`='binance_v3'이면 20시간마다 바이낸스 1시간봉 이어받기(history_binance, binance_history_downloader) + V3 모델 1주마다 재학습 |
| `WATCHDOG_MIN` | 20 | 이 시간(분) 넘게 로그 저장이 없으면 수집기가 스스로 재시작(멈춤 자동 복구). run_collector.bat로 켜면 bat가 다시 띄우고, 직접 켜면 새 창으로 다시 뜸 |

## 6. 클라이언트 버튼 설정 (기기별)

| 버튼 | 저장 파일 | 뜻 |
|---|---|---|
| 확률필터 ON/OFF | `prob_filter.json` | OFF면 필터 적용 전 판정을 표시(서버 판정·로그는 그대로) |
| 자동진입 (설정 창) | `auto_entry.json`, 기록 `auto_entry_log.csv` | 1회 진입은 **고정 $** / **현금의 %**(`amt_mode`=usd/pct, `AUTO_ENTRY_PCT`=3, 진입할 때마다 그 시점 현금 기준) / **사용자 정의**(`amt_mode`=rule, C언어 형식 if/else — start_seed(처음 시드)·current_seed(총자산=현금+증거금+평가손익)·cash(현금) → position_size, sizing_rule.py, 등록 전 문법 검사. 실전은 live_sizing_rule.txt) — 11/22. 켜면 판정 롱/숏이 새로 뜰 때 증거금·레버리지(기본 $30×3, 하락장이면 롱 자동진입 안 함 체크 기본 켜짐 — `skip_bear_long`, 강한 숏(4h예상 -1% 이하)은 진입 금액 2배 기본 켜짐 — `strong_x2`, `AUTO_ENTRY_STRONG_SHORT_EXP4`=-1.0·`AUTO_ENTRY_STRONG_MULT`=2.0)로 바로 진입. 잔고 제한=충전 잔고 안에서만(부족하면 건너뜀), 잔고 무시=서버 `open_force`로 강제 진입(잔고 마이너스 가능). 클라이언트 켜져 있을 때만. 바이낸스 선물에 없는 코인(NOFUT)은 롱·숏 모두 자동진입 안 함(롱 판정 표시는 그대로). "진입 후 N시간 자동 정리"(기본 48, 0=안 함)는 자동진입 포지션만 정리(`auto_entry_positions.json`), 클라가 꺼져 있던 동안 시간이 지났으면 켜질 때 정리 |
| 거래소 토글 | – | 빗썸 / 업비트 / 바이낸스(11/23). 자동진입은 켜져 있으면 세 거래소 판정을 모두 봄(같은 코인 보유 중이면 건너뜀) |
| 순위 탭 | – | 4h 예상수익(exp4) 순 롱/숏 상위 10. 서버가 구버전이면 p_up 순 |
| 롱 조건식 색칠 끔 (코드 상수) | `trading_client.py` `LONG_COND_COLOR_OFF`=True | 초록 계열(롱) 조건식은 카드 배경을 안 칠함(목록·사용여부는 그대로, 숏 조건식은 그대로). 1년 1h봉 48h 승률 43.7%/49.8% — 끄려면 False (11/24) |
| 관찰 롱 표시 (코드 상수) | `OBSERVE_LONG_ON`=True, `OBSERVE_LONG_EXPR`="funding < -0.01 && rsi < 35 && btc_up > 0", `OBSERVE_LONG_COLOR`=#a0c4ff | 맞으면 카드 연파랑 배경 + "👀 관찰 롱" 뱃지만(판정·자동진입 무관). 1년 48h 143건 60.1%(앞 57%/뒤 67%) — 검증 중 (11/24) |
| 하락장 롱 레짐 기준 (코드 상수) | `trading_client.py` `AUTO_BEAR_REGIME_EX`="binance" | 자동진입 "하락장이면 롱 자동진입 안 함"을 바이낸스 스냅샷 레짐으로 판단(빗썸·업비트·바이낸스 판정 모두). ""면 각 거래소 자기 레짐. 바이낸스 스냅샷을 못 읽으면 예전처럼 화면 레짐 (11/25) |
| 조건식 변수 `btc_up` | – | 업비트 KRW-BTC 1시간봉 종가가 EMA120 위면 1, 아니면 0(클라가 5분마다 갱신). 클라 조건식에서만 씀 — 서버 디스코드 알림 쪽은 이 변수를 몰라서 btc_up 조건은 알림 안 감 (11/24) |

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
| 11/22 | 1회 진입 금액 '사용자 정의'(C언어 형식 if/else, sizing_rule.py) — 클라 자동진입 `amt_mode=rule`·live_trader `margin_rule`/live_sizing_rule.txt (사용자 요청) | backups\20261122_sizing_rule |
| 11/22 | 사용자 정의 current_seed 현금 → 총자산(현금+증거금+평가손익), `cash` 변수 추가 (사용자 요청) | backups\20261122_equity_seed |
| 11/23 | 재진입 가드(사용자 요청): ① 클라 자동진입 판정 기억을 파일(auto_entry_seen.json, 기기별)로 유지·빈 스냅샷 무시·같은 코인·방향 판정을 6h 안에 봤으면 새 신호 아님(`AUTO_RESIGNAL_GAP_H`) — 11/23 10:05 재시작 직후 ELSA·PIEVERSE 동시 재진입 대응 ② 강제청산된 코인은 같은 방향 48h 자동진입 차단(클라 `AUTO_LIQ_BLOCK_H`, live_trader `LIQ_BLOCK_H`). 강제청산 기록은 trading 폴더 liquidations_mock_<기기>.json / liquidations_live.json 에 누적(git으로 올라감, 안 지움). 위험 플래그 숏 2배안은 백테스트상 수익만 줄어 미적용 | backups\20261123_reentry_guard |
| 11/23 | 바이낸스 탭: `BINANCE_ENABLED`·`BINANCE_UNIVERSE`='krw'·`BINANCE_PROB_PROFILE`='krw'(서버), 수집기 바이낸스 기록·`BINANCE_MODEL_JOBS`=False, 클라 거래소 토글 3개, prob_model 바이낸스 모델 경로, live_trader `SIGNAL_SOURCES`=("binance",) (사용자 요청) | backups\20261123_binance_tab |
| 11/23 | 서버 원격 재시작/종료 명령 `restart`/`shutdown`(`SERVER_RESTART_EXIT_CODE`=75, 사용자 요청) | backups\20261123_binance_tab (바이낸스 탭 이전 원본) |
| 11/23 | 판정 하락장 롱 대기를 거래소별 자기 레짐으로(`_regime_of`, 업비트가 빗썸 레짐을 쓰던 것 수정 — 사용자: 시뮬 단계라 세 거래소 다 자기 레짐으로 두고 예외 케이스를 잡아 개선) | backups\20261123_regime_per_exchange |
| 11/23 | 빗썸·업비트 감시 대상 40개를 안정 유니버스로(`UNIVERSE_MODE`="stable": 상장 1년↑·30일 일별 거래대금 중앙값 상위 40·매일 09:05 갱신·60위 안이면 유지·유의종목 제외·이상치 판정 대기) — 백테스트 청산 20.5%→3.6% (사용자 요청) | backups\20261123_universe |
| 11/24 | 안정 유니버스 바이낸스 제외 보강: 1000배수 코인(PEPE·SHIB·BONK·XEC 등) 이름 맞춤 + `UNIVERSE_EXCLUDE_STABLE`=True(USDT·USD1 등 제외) + `UNIVERSE_EX_VER`=2(재시작 직후 재계산) (사용자 요청) | backups\20261124_universe_fix |
| 11/24 | 클라 `LONG_COND_COLOR_OFF`=True(롱 조건식 색칠 끔), `OBSERVE_LONG_*` 관찰 롱 표시, 조건식 변수 `btc_up` 추가 (사용자 요청, 근거 reports\cond_1y_48h_20261124.md). 숏 조건식 v5(`short_conditions_import_v5.txt`, v4에서 59% 1절 제거) | backups\20261124_long_cond |
| 11/24 | 강제청산 코인 같은 방향 자동진입 차단 48h → 7일(클라 `AUTO_LIQ_BLOCK_H`·live_trader `LIQ_BLOCK_H` = 168). 백테스트 청산 14→11, $+564→$+613, 최악월 -$69→-$18 (사용자 요청) | backups\20261124_liq_block_7d |
| 11/25 | `V2_SHORT_RSI_MIN`=50(모델 숏은 RSI≥50만, 서버·수집기·바이낸스 탭 공통), 클라 `AUTO_BEAR_REGIME_EX`="binance"(자동진입 '하락장 롱 제외'를 바이낸스 레짐으로 통일 — 백그라운드 거래소 판단 때 빗썸 레짐이 쓰이던 문제 수정) (사용자 요청) | backups\20261125_short_rsi50_bear_regime |
| 11/25 | 바이낸스 탭 개편 V3: `BINANCE_UNIVERSE`='v3'(필터 후 거래대금 상위 300)·`BINANCE_PROB_PROFILE`='binance_v3'(바이낸스 학습, 코인+BTC 특성)·판정 가드 상장 730일↑·ATR<2.5·7일<+50%·채점 3분 간격·OI 캐시 30분(서버), V3 모델(prob_model), V3 재학습·1시간봉 이어받기(수집기 `BINANCE_V3_MODEL_JOBS`), 설명 창 문구(클라) (사용자 요청, reports/binance_backtest11~13_20261125.md) | backups\20261125_v3_binance |
| 11/26 | 바이낸스 V3 백테스트 맞추기: `BINANCE_V3_CLOSED_BAR`=True(닫힌 1h봉 피처·손절 차단 기준가), 바이낸스 V3 탭 RSI≥50 규칙 제외, `BINANCE_V3_LONG`=False(롱 검증 탈락) (사용자 요청, reports/binance_backtest14_20261125.md) | backups\20261126_v3_parity |
| 11/26 | `BINANCE_V3_LONG`=True (롱 끔 해제, 사용자 요청 — 검증은 -$68이었음) | backups\20261126_v3_long_on |
| 11/26 | 정리 슬리피지 추가: 모의 서버 `EXIT_SLIPPAGE_RATE`=0.007(청산 버튼·48h 자동정리·부분청산 때 롱은 0.7% 낮게·숏은 0.7% 높게 체결, 강제청산 제외 — 진입 `SLIPPAGE_RATE` 0.7%와 같게), 바이낸스 백테스트 bt~bt14 비용 `COST` 0.021→0.042(진입 0.7%+정리 0.7%, 3배 증거금 -4.2%), 정리 비용 비교용 binance_backtest15.py 추가 (사용자 요청) | backups\20261126_exit_slippage |
| 11/26 | 모의 서버 `CLAUDE_CMDS`=True: trading\claude_cmds\ 에 넣은 cmd_*.csv 도 처리(결과 claude_cmds\results.csv) — Claude가 계좌 조작 가능 (사용자 요청) | backups\20261126_claude_cmds |
| 11/26 | 클라 수익 유지 컷 `AUTO_TRAIL_ON`=True, `AUTO_TRAIL_ACT_PCT`=15, `AUTO_TRAIL_GIVE_PCT`=3: 자동진입 포지션이 가격 기준 이익 15% 이상 났다가 최대 이익에서 3%p 되돌리면 48h 전 정리. 1년 백테스트 +$837→+$848, 월 중앙 +$30→+$54 (reports/exit_study_20261126.md, 사용자 요청) | backups\20261126_trail_cut |
| 11/26 | 모델 숏 RSI 필터 끔 `V2_SHORT_RSI_MIN` 50→0 — 15일 후보 36건 중 RSI<50 4건(11/16 이후 0건)이라 효과 없음. 같이 V3 숏 exp4 상한 완화 1년 백테스트(bt16): -0.4는 461건 +$787·낙폭 -$205, -0.3은 664건 +$768 → -0.5(327건 +$837) 유지 (사용자 요청) | backups\20261126_rsi_off |
| 11/26 13:13 | 모의 계좌 리셋(전체 청산 → 잔고·통장 리셋 → 청산 기록 초기화 → 통장입금·충전 $1,000), 사용자 요청. 이전 런 최종 총자산 $944.1·정리 20건·강제청산 4건·실현 -$49.4 | (데이터 백업 없음 — 서버 리셋 명령) |
