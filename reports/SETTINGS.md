# Pyramide 설정값 정리

마지막 갱신: 2026-10-17 · 새 설정이 생기거나 값이 바뀌면 이 문서(reports/SETTINGS.md)와 PDF(reports/SETTINGS.pdf)를 같이 갱신한다.

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
| `VERDICT_V2` | True | 판정 v2 사용. 롱 = 기존 롱 후보 ∩ p_up≥0.55 ∩ 4h예상>+0.2%, 숏 = 4h예상<-0.5% ∩ p_up<0.45(모델만) | False면 예전 점수식 판정 |
| `V2_LONG_EXP4_MIN` | 0.2 | 롱 판정에 필요한 최소 4h 예상수익(%) | 높이면 롱 신호 줄고 정확도↑ |
| `V2_SHORT_EXP4_MAX` | -0.5 | 숏 판정 기준 4h 예상수익(%) 상한 | 더 낮추면(-0.7) 숏 신호 줄고 승률↑ |
| `V2_SHORT_PUP_MAX` | 0.45 | 숏 판정에 필요한 24h 상승확률 상한 | |
| `V2_SHORT_BLOCK_MKT_R4` | 1.0 | 40종목 평균 4h 등락이 이 % 이상(반등 중)이면 모델 숏 대기 | 999로 하면 끔 |
| `BEAR_LONG_BLOCK` | True | 레짐이 하락장이면 롱 판정 대기 | False면 하락장에도 롱 |
| `BEAR_LONG_UNLOCK_MKT_R24` | 0.0 | 하락장이라도 40종목 평균 24h 등락이 이 %보다 크면 롱 허용 | 999로 하면 해제 기능 끔 |
| `USE_PROB_FILTER` | True | 24h 상승확률 필터(롱 p_up≥0.55, 숏 p_up≤0.45) | False면 필터 없이(표시는 유지) |
| `PROB_LONG_MIN` | 0.55 | 롱 판정 최소 상승확률 | |
| `PROB_SHORT_MAX` | 0.45 | (v2 꺼졌을 때) 숏 판정 최대 상승확률 | |
| `VERDICT_CUT` | 50 | (판정 후보) 롱/숏 점수 컷 | |
| `VERDICT_PP_CUT` | 80 | (판정 후보) 매집/분산 점수 컷 | |
| `VERDICT_LONG_COND` / `VERDICT_SHORT_COND` | 조건식 롱 v7 / 숏 v3 | (판정 후보) 매집·분산과 같이 봐야 하는 조건식. 클라 색칠용 같은 내용: `long_conditions_import_v7.txt`, `short_conditions_import_v3.txt` | 예전 식은 v5 / v1+v2 파일에 그대로 있음 |

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

## 6. 클라이언트 버튼 설정 (기기별)

| 버튼 | 저장 파일 | 뜻 |
|---|---|---|
| 확률필터 ON/OFF | `prob_filter.json` | OFF면 필터 적용 전 판정을 표시(서버 판정·로그는 그대로) |
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
