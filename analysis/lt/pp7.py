def _prepump_v7(current_price, box_high, box_low, ema20, rsi, recent_pct, atr_pct, price_cv_24h, regime):
    s = 0
    rp = recent_pct if recent_pct is not None else 0.0
    s += 20 if rp <= -10 else 18 if rp <= -5 else 12 if rp <= -2 else 8 if rp <= 0 else 4 if rp <= 3 else 0 if rp <= 10 else -10
    if box_high is not None and box_low is not None and box_high > box_low:
        bp = (current_price - box_low) / (box_high - box_low) * 100
        s += 15 if bp <= 10 else 12 if bp <= 25 else 7 if bp <= 50 else 2 if bp <= 80 else 0
    if ema20:
        ed = (current_price / ema20 - 1) * 100
        s += 8 if ed < -4 else 15 if ed <= -0.5 else 6 if ed <= 0.5 else 2 if ed <= 2 else 0
    s += 12 if rsi < 30 else 15 if rsi <= 40 else 12 if rsi <= 45 else 8 if rsi <= 50 else 4 if rsi <= 55 else 0
    a = atr_pct or 0.0
    s += 0 if a < 0.5 else 4 if a < 0.9 else 7 if a < 1.5 else 10 if a <= 3 else 0
    cv = price_cv_24h or 0.0
    s += 0 if cv < 0.006 else 10 if 0.0166 <= cv < 0.0241 else 6 if 0.0129 <= cv < 0.0166 else 3
    score = s * 100 / 78
    if regime == '하락장':
        score *= 80 / 90
    return max(0, min(round(score), 100))

