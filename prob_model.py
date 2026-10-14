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
# [2026-10-14] scikit-learn 없는 기기(태블릿 Termux 등)용 — 트리 구조만 JSON으로 내보내서 numpy로 예측.
# models/*.pkl은 git에 안 올라가지만 이 JSON은 올라가서 pull만 하면 다른 기기에서도 p_up이 나온다.
LITE_PATH = os.path.join(MODEL_DIR, "prob_model_lite.json")
TRAIN_LOG = os.path.join(MODEL_DIR, "train_log.txt")
HORIZON_H = 24
MODEL_VERSION = 2           # [2026-10-15] v2 = 24h 상승확률(p_up) + 4h 시장대비 기대수익(exp4) 두 모델
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
        f['f4'] = (d['close'].shift(-4) / d['close'] - 1) * 100
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
    X['x4'] = X['f4'] - X.groupby('ts')['f4'].transform('mean')
    return X


def _new_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
                                          min_samples_leaf=400, l2_regularization=1.0, random_state=0)


def _new_reg():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=31,
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
    # [2026-10-15] 4h 시장대비 기대수익 회귀모델 — 순위 탭 정렬 기준.
    # 2년 워크포워드: 매시 상위5 롱/하위5 숏 4h 승률 61%/62%(24h확률 순위 55%/57%), 순위상관 1h 0.20·4h 0.18
    L4 = L[L['x4'].notna()]
    tr4 = tr[tr['x4'].notna()]; te4 = te[te['x4'].notna()]
    try:
        m4 = _new_reg().fit(tr4[FEATURES], tr4['x4'].clip(-15, 15))
        te4 = te4.assign(e=m4.predict(te4[FEATURES]))
        meta['holdout_ic_x4'] = round(float(te4.groupby('ts').apply(
            lambda g: g['e'].corr(g['x4'], method='spearman')).mean()), 3)
    except Exception as e:
        meta['holdout_error_x4'] = str(e)
    model = _new_model().fit(L[FEATURES], (L['f24'] > 0).astype(int))
    model_x4 = _new_reg().fit(L4[FEATURES], L4['x4'].clip(-15, 15))
    obj = {'model': model, 'model_x4': model_x4, 'version': MODEL_VERSION, 'features': FEATURES, 'trained_at': time.strftime('%Y-%m-%d %H:%M:%S'),
           'trained_ts': time.time(), 'rows': int(len(L)), 'tickers': int(L['ticker'].nunique()),
           'data_from': str(L['ts'].min()), 'data_to': str(L['ts'].max()),
           'sklearn': sklearn.__version__, **meta}
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = MODEL_PATH + '.tmp'
    with open(tmp, 'wb') as fp:
        pickle.dump(obj, fp)
    os.replace(tmp, MODEL_PATH)
    try:
        export_lite(obj)
    except Exception as e:
        _log(f"경량 모델 내보내기 실패: {e}")
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
    if a is None or a >= RETRAIN_HOURS:
        return True
    try:
        o = load_model()
        return not o or o.get('lite') or o.get('version', 1) < MODEL_VERSION
    except Exception:
        return False


# ───────────────────────── 경량 모델(sklearn 없이 예측) ─────────────────────────
def _trees_of(m):
    trees = []
    for it in m._predictors:
        nd = it[0].nodes
        trees.append({
            'f': nd['feature_idx'].astype(int).tolist(),
            't': [float(v) for v in nd['num_threshold']],
            'm': nd['missing_go_to_left'].astype(int).tolist(),
            'l': nd['left'].astype(int).tolist(),
            'r': nd['right'].astype(int).tolist(),
            'k': nd['is_leaf'].astype(int).tolist(),
            'v': [float(v) for v in nd['value']],
        })
    return trees


def export_lite(obj, path=LITE_PATH):
    """학습된 HGB 모델들의 트리를 JSON으로 저장(p_up 분류 + exp4 회귀)."""
    m = obj['model']
    lite = {'features': obj['features'], 'baseline': float(np.ravel(m._baseline_prediction)[0]),
            'trees': _trees_of(m),
            **{k: v for k, v in obj.items() if k not in ('model', 'model_x4', 'features')}}
    if obj.get('model_x4') is not None:
        m4 = obj['model_x4']
        lite['baseline_x4'] = float(np.ravel(m4._baseline_prediction)[0])
        lite['trees_x4'] = _trees_of(m4)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump(lite, fp, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


class _LiteModel:
    def __init__(self, d, key='trees', base='baseline'):
        self.baseline = d[base]
        self.trees = [{k: np.asarray(t[k], dtype=float if k in ('t', 'v') else np.int64) for k in 'ftmlrkv'}
                      for t in d[key]]

    def predict(self, X):
        return self._raw(X)

    def predict_proba(self, X):
        p = 1.0 / (1.0 + np.exp(-self._raw(X)))
        return np.column_stack([1 - p, p])

    def _raw(self, X):
        X = np.asarray(X, dtype=float)
        n = len(X)
        raw = np.full(n, self.baseline)
        rows = np.arange(n)
        for t in self.trees:
            idx = np.zeros(n, dtype=np.int64)
            for _ in range(64):
                leaf = t['k'][idx] == 1
                if leaf.all():
                    break
                x = X[rows, t['f'][idx]]
                go_left = np.where(np.isnan(x), t['m'][idx] == 1, x <= t['t'][idx])
                nxt = np.where(go_left, t['l'][idx], t['r'][idx])
                idx = np.where(leaf, idx, nxt)
            raw += t['v'][idx]
        return raw


# ───────────────────────── 예측 ─────────────────────────
_lock = threading.Lock()
_cache = {'mtime': None, 'obj': None}


def load_model():
    """sklearn 모델(pkl)을 우선 쓰고, sklearn이 없거나 pkl이 없으면 경량 JSON 모델을 쓴다.
    pkl을 읽은 기기(PC)는 경량 JSON이 없거나 낡았으면 자동으로 다시 내보낸다."""
    try:
        mt = os.path.getmtime(MODEL_PATH)
        src = MODEL_PATH
    except Exception:
        mt = None
    try:
        lmt = os.path.getmtime(LITE_PATH)
    except Exception:
        lmt = None
    key = (mt, lmt)
    with _lock:
        if _cache['mtime'] == key:
            return _cache['obj']
        obj = None
        if mt is not None:
            try:
                with open(MODEL_PATH, 'rb') as fp:
                    obj = pickle.load(fp)
                if lmt is None or lmt < mt:
                    try:
                        export_lite(obj)
                        lmt = os.path.getmtime(LITE_PATH)
                    except Exception as e:
                        print(f"[prob_model] 경량 모델 내보내기 실패: {e}")
            except Exception as e:
                obj = None
                if lmt is None:
                    print(f"[prob_model] 모델 로드 실패: {e}")
        if obj is None and lmt is not None:
            try:
                with open(LITE_PATH, 'r', encoding='utf-8') as fp:
                    d = json.load(fp)
                obj = {k: v for k, v in d.items() if k not in ('trees', 'baseline', 'trees_x4', 'baseline_x4')}
                obj['model'] = _LiteModel(d)
                if 'trees_x4' in d:
                    obj['model_x4'] = _LiteModel(d, 'trees_x4', 'baseline_x4')
                obj['lite'] = True
            except Exception as e:
                print(f"[prob_model] 경량 모델 로드 실패: {e}")
                obj = None
        _cache['obj'] = obj
        _cache['mtime'] = (mt, lmt)
        return obj


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
            r['exp4'] = None
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
        Xf = X[obj['features']]
        Xin = Xf if not obj.get('lite') else Xf.to_numpy(dtype=float)
        p = obj['model'].predict_proba(Xin)[:, 1]
        pm = dict(zip(X['ticker'], p))
        em = {}
        if obj.get('model_x4') is not None:
            try:
                em = dict(zip(X['ticker'], obj['model_x4'].predict(Xin)))
            except Exception as e:
                print(f"[prob_model] 4h 기대수익 예측 실패: {e}")
        n = 0
        for r in results:
            v = pm.get(r.get('ticker'))
            r['p_up'] = round(float(v), 3) if v is not None else None
            e4 = em.get(r.get('ticker'))
            r['exp4'] = round(float(e4), 3) if e4 is not None else None
            n += v is not None
        return n
    except Exception as e:
        print(f"[prob_model] 예측 실패: {e}")
        for r in results:
            r['p_up'] = None
            r['exp4'] = None
        return 0


if __name__ == '__main__':
    train()
