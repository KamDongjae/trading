# -*- coding: utf-8 -*-
"""
24h 상승확률 모델 (2026-10-13 신설)

- 학습: history/ 폴더의 1시간봉(history_downloader.py가 받음)으로 "24시간 뒤 가격이 지금보다
  높을 확률"을 HistGradientBoosting으로 학습해서 models/prob_model.pkl 에 저장한다.
- 예측: 서버가 이미 받아온 캔들 df로 같은 피처 함수(build_features)를 돌리고, 전 종목을 모은 뒤
  시장 맥락(시장 평균 등락, 폭, BTC 흐름, 종목간 순위)을 붙여서 p_up을 계산한다.
- 2년 워크포워드 검증(reports/longterm_20261013.md): 판정에 확률 필터(롱 p>=0.55, 숏 p<=0.45)를
  얹으면 두 구간 모두 승률·초과수익 개선.
- scikit-learn이 없거나 모델 파일이 없으면 조용히 꺼진다(p_up=None, 판정 필터 미적용).
"""
import os, glob, json, time, pickle, threading
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "history")
MODEL_DIR = os.path.join(BASE_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "prob_model.pkl")
TRAIN_LOG = os.path.join(MODEL_DIR, "train_log.txt")
HORIZON_H = 24
RETRAIN_HOURS = 20          # 이보다 오래된 모델이면 재학습 대상
MIN_ROWS_PER_TICKER = 300

TICKER_FEATS = ['rsi', 'rsi_delta', 'bb_percent', 'd_e20', 'd_e60', 'd_e120', 'ema20_slope_pct', 'atr_pct',
                'vol_z', 'extension_pct', 'recent_pct', 'price_cv_24h', 'boxpos',
                'r1', 'r4', 'r24', 'r72', 'r168', 'rsi_dwell_os', 'rsi_dwell_ob', 'bb_dwell_low', 'bb_dwell_high']
XS_FEATS = ['mkt_r24', 'mkt_r72', 'breadth', 'btc_r24', 'btc_r72', 'btc_r168', 'reg_c',
            'rk_r24', 'rk_r72', 'rk_r168', 'rk_rsi', 'rk_atr_pct', 'rk_vol_z']
FEATURES = TICKER_FEATS + XS_FEATS


def _dwell(b):
    b = b.fillna(False).astype(bool)
    grp = (~b).cumsum()
    return b.astype(int).groupby(grp).cumsum().astype(float)


def build_features(df):
    """df: open/high/low/close/volume 1시간봉(시간순). 모든 행의 종목 피처를 돌려준다(벡터화)."""
    c = df['close'].astype(float); h = df['high'].astype(float); l = df['low'].astype(float)
    v = df['volume'].astype(float)
    f = pd.DataFrame(index=df.index)
    f['p'] = c
    d = c.diff(); g = d.clip(lower=0); ls = -d.clip(upper=0)
    rs = g.ewm(alpha=1 / 14, adjust=False).mean() / ls.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    f['rsi'] = 100 - 100 / (1 + rs)
    f['rsi_delta'] = f['rsi'] - f['rsi'].shift(4)
    ma = c.rolling(20).mean(); sd = c.rolling(20).std()
    f['bb_percent'] = (c - (ma - 2 * sd)) / (4 * sd).replace(0, np.nan) * 100
    e20 = c.ewm(span=20, adjust=False).mean(); e60 = c.ewm(span=60, adjust=False).mean(); e120 = c.ewm(span=120, adjust=False).mean()
    f['ema60'] = e60
    f['d_e20'] = (c / e20 - 1) * 100; f['d_e60'] = (e20 / e60 - 1) * 100; f['d_e120'] = (e60 / e120 - 1) * 100
    f['ema20_slope_pct'] = (e20 / e20.shift(3) - 1) * 100
    f['_bull'] = (e20 > e60) & (e60 > e120) & (c > e20)
    f['_bear'] = (e20 < e60) & (e60 < e120) & (c < e20)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f['atr_pct'] = tr.rolling(14).mean() / c * 100
    f['vol_z'] = (v - v.rolling(20).mean()) / v.rolling(20).std().replace(0, np.nan)
    f['extension_pct'] = (c / c.shift(10) - 1) * 100
    f['recent_pct'] = (c / c.shift(72) - 1) * 100
    f['price_cv_24h'] = c.rolling(24).std() / c.rolling(24).mean()
    bh = h.rolling(20).max(); bl = l.rolling(20).min()
    f['boxpos'] = (c - bl) / (bh - bl).replace(0, np.nan)
    for n in [1, 4, 24, 72, 168]:
        f[f'r{n}'] = (c / c.shift(n) - 1) * 100
    f['rsi_dwell_os'] = _dwell(f['rsi'] < 40); f['rsi_dwell_ob'] = _dwell(f['rsi'] > 60)
    f['bb_dwell_low'] = _dwell(f['bb_percent'] < 30); f['bb_dwell_high'] = _dwell(f['bb_percent'] > 70)
    f['_stale'] = v.rolling(24).sum() == 0
    return f


def add_cross_section(X):
    """X: ts, ticker 컬럼 + 종목 피처. 같은 ts끼리 묶어 시장 맥락 피처를 붙인다."""
    g = X.groupby('ts')
    X['mkt_r24'] = g['r24'].transform('mean'); X['mkt_r72'] = g['r72'].transform('mean')
    X['breadth'] = (X['p'] > X['ema60']).astype(float).groupby(X['ts']).transform('mean')
    nb = X['_bull'].astype(float).groupby(X['ts']).transform('sum')
    ns = X['_bear'].astype(float).groupby(X['ts']).transform('sum')
    aatr = g['atr_pct'].transform('mean')
    tb = (nb - ns) / (nb + ns).replace(0, np.nan)
    X['reg_c'] = np.select([aatr >= 2, tb >= 0.15, tb <= -0.15], [2, 1, -1], 0)
    btc = X[X['ticker'] == 'BTC'].set_index('ts')[['r24', 'r72', 'r168']]
    btc = btc[~btc.index.duplicated()]
    for col in ['r24', 'r72', 'r168']:
        X['btc_' + col] = X['ts'].map(btc[col]) if len(btc) else np.nan
    for col in ['r24', 'r72', 'r168', 'rsi', 'atr_pct', 'vol_z']:
        X['rk_' + col] = X.groupby('ts')[col].rank(pct=True)
    return X


# ───────────────────────── 학습 ─────────────────────────
def _load_history(history_dir=HISTORY_DIR):
    frames = {}
    for f in sorted(glob.glob(os.path.join(history_dir, '*_1h.csv'))):
        base = os.path.basename(f)
        try:
            src, tk, _ = base.split('_', 2)
        except ValueError:
            continue
        if tk in frames and src != 'upbit':
            continue  # 같은 종목이면 업비트 우선
        try:
            d = pd.read_csv(f)
            d['ts'] = pd.to_datetime(d['ts_kst'])
            d = d.set_index('ts').sort_index()[['open', 'high', 'low', 'close', 'volume']].astype(float)
            d = d[~d.index.duplicated()].asfreq('1h')
            d['close'] = d['close'].ffill()
            for col in ['open', 'high', 'low']:
                d[col] = d[col].fillna(d['close'])
            d['volume'] = d['volume'].fillna(0)
            if len(d) >= MIN_ROWS_PER_TICKER:
                frames[tk] = d
        except Exception:
            continue
    return frames


def build_dataset(history_dir=HISTORY_DIR):
    out = []
    for tk, d in _load_history(history_dir).items():
        f = build_features(d)
        f['ticker'] = tk
        f['f24'] = (d['close'].shift(-HORIZON_H) / d['close'] - 1) * 100
        f = f.iloc[200:]
        out.append(f)
    if not out:
        return None
    X = pd.concat(out).reset_index().rename(columns={'index': 'ts'})
    if 'ts' not in X.columns:
        X = X.rename(columns={X.columns[0]: 'ts'})
    X = add_cross_section(X)
    X = X[~X['_stale']]
    X['x24'] = X['f24'] - X.groupby('ts')['f24'].transform('mean')
    return X


def _new_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
                                          min_samples_leaf=400, l2_regularization=1.0, random_state=0)


def _log(msg):
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        with open(TRAIN_LOG, 'a', encoding='utf-8') as fp:
            fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass
    print(f"[prob_model] {msg}")


def train(history_dir=HISTORY_DIR, holdout_days=60):
    """전체 history로 학습. 마지막 holdout_days는 먼저 검증용으로 떼서 성능을 기록한 뒤 전체로 재학습."""
    t0 = time.time()
    try:
        import sklearn
    except Exception:
        _log("scikit-learn 없음 — 학습 건너뜀")
        return False
    X = build_dataset(history_dir)
    if X is None or len(X) < 10000:
        _log("history 데이터 부족 — 학습 건너뜀")
        return False
    L = X[X['f24'].notna()]
    L = L[L['ts'].dt.hour % 2 == 0]
    cut = L['ts'].max() - pd.Timedelta(days=holdout_days)
    tr = L[L['ts'] < cut - pd.Timedelta(days=3)]; te = L[L['ts'] >= cut]
    meta = {}
    try:
        m = _new_model().fit(tr[FEATURES], (tr['f24'] > 0).astype(int))
        p = m.predict_proba(te[FEATURES])[:, 1]
        te = te.assign(p=p)
        ic = te.groupby('ts').apply(lambda g: g['p'].corr(g['x24'], method='spearman')).mean()
        hi = te[te['p'] >= 0.55]; lo = te[te['p'] <= 0.45]
        meta = {'holdout_days': holdout_days, 'holdout_ic': round(float(ic), 3),
                'holdout_up_rate_p55': round(float((hi['f24'] > 0).mean() * 100), 1) if len(hi) else None,
                'holdout_up_rate_p45': round(float((lo['f24'] > 0).mean() * 100), 1) if len(lo) else None,
                'holdout_up_rate_all': round(float((te['f24'] > 0).mean() * 100), 1)}
    except Exception as e:
        meta = {'holdout_error': str(e)}
    model = _new_model().fit(L[FEATURES], (L['f24'] > 0).astype(int))
    obj = {'model': model, 'features': FEATURES, 'trained_at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'trained_ts': time.time(), 'rows': int(len(L)), 'tickers': int(L['ticker'].nunique()),
           'data_from': str(L['ts'].min()), 'data_to': str(L['ts'].max()),
           'sklearn': sklearn.__version__, **meta}
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = MODEL_PATH + '.tmp'
    with open(tmp, 'wb') as fp:
        pickle.dump(obj, fp)
    os.replace(tmp, MODEL_PATH)
    _log(f"학습 완료 {time.time() - t0:.0f}s rows={len(L)} tickers={obj['tickers']} "
         f"{obj['data_from'][:10]}~{obj['data_to'][:10]} " + json.dumps(meta, ensure_ascii=False))
    return True


def model_age_hours():
    try:
        return (time.time() - os.path.getmtime(MODEL_PATH)) / 3600
    except Exception:
        return None


def due():
    a = model_age_hours()
    return a is None or a >= RETRAIN_HOURS


# ───────────────────────── 예측 ─────────────────────────
_lock = threading.Lock()
_cache = {'mtime': None, 'obj': None}


def load_model():
    try:
        mt = os.path.getmtime(MODEL_PATH)
    except Exception:
        return None
    with _lock:
        if _cache['mtime'] != mt:
            try:
                with open(MODEL_PATH, 'rb') as fp:
                    _cache['obj'] = pickle.load(fp)
                _cache['mtime'] = mt
            except Exception as e:
                print(f"[prob_model] 모델 로드 실패: {e}")
                _cache['obj'] = None
                _cache['mtime'] = mt
        return _cache['obj']


def model_info():
    obj = load_model()
    if not obj:
        return None
    return {k: v for k, v in obj.items() if k != 'model'}


def ticker_features(df):
    """서버 process_ticker의 캔들 df(마지막 행 = 현재)로 종목 피처 dict를 만든다. 실패하면 None."""
    try:
        if df is None or len(df) < 30:
            return None
        f = build_features(df[['open', 'high', 'low', 'close', 'volume']].astype(float))
        row = f.iloc[-1]
        out = {k: (float(row[k]) if pd.notna(row[k]) else np.nan) for k in TICKER_FEATS + ['p', 'ema60']}
        out['_bull'] = bool(row['_bull']); out['_bear'] = bool(row['_bear'])
        return out
    except Exception:
        return None


def predict(results, key='_pmf'):
    """results: process_ticker 결과 dict 리스트(각자 r[key]에 ticker_features 결과). r['p_up']를 채운다."""
    obj = load_model()
    if not obj:
        for r in results:
            r['p_up'] = None
        return 0
    rows = []
    for r in results:
        f = r.get(key)
        if f:
            rows.append({'ts': 0, 'ticker': r.get('ticker'), **f})
    if not rows:
        return 0
    try:
        X = add_cross_section(pd.DataFrame(rows))
        p = obj['model'].predict_proba(X[obj['features']])[:, 1]
        pm = dict(zip(X['ticker'], p))
        n = 0
        for r in results:
            v = pm.get(r.get('ticker'))
            r['p_up'] = round(float(v), 3) if v is not None else None
            n += v is not None
        return n
    except Exception as e:
        print(f"[prob_model] 예측 실패: {e}")
        for r in results:
            r['p_up'] = None
        return 0


if __name__ == '__main__':
    train()
