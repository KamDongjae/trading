# Score and condition replay

Nov 23–28 KST, with Nov 27 onward held out for comparison. Archived curated colors are replayed from inputs, and may differ from colors active in the client. Conditional variants narrow the original color signals. All rows have the same cap of 3 per exact time and direction. Fixed USD 30, 3x, 0.7% slippage and 0.04% fee each side. Entry uses the next quote. No cash cap or intrabar liquidation. Risk gate requires logged max_lev ≥ 3 and checks explicit NOFUT fields only. Model rank falls back below any available model score, ties by ticker.

## Binance 12h, 1854 usable, 3644 incomplete paths

| period | rule | trades | net USD | max drawdown USD | wins |
|---|---|---:|---:|---:|---:|
| all | baseline | 1169 | -1348.73 | 1351.41 | 352 |
| all | risk | 1167 | -1338.84 | 1341.47 | 351 |
| all | expected | 1167 | -1345.84 | 1351.02 | 352 |
| all | expected/ATR | 1167 | -1352.63 | 1357.90 | 355 |
| all | long_rebound | 655 | -720.93 | 1062.25 | 203 |
| all | long_breakout | 664 | -958.96 | 1187.59 | 196 |
| all | short_turn | 673 | -586.22 | 641.03 | 213 |
| all | short_trend | 686 | -763.26 | 818.13 | 195 |
| all | combo_rebound_turn | 161 | +38.23 | 88.81 | 66 |
| all | combo_breakout_trend | 183 | -379.77 | 397.42 | 40 |
| early | baseline | 563 | -763.81 | 768.74 | 146 |
| early | risk | 563 | -763.81 | 768.74 | 146 |
| early | expected | 563 | -771.90 | 777.11 | 149 |
| early | expected/ATR | 563 | -779.12 | 784.25 | 150 |
| early | long_rebound | 306 | -544.32 | 556.70 | 68 |
| early | long_breakout | 320 | -592.33 | 597.62 | 69 |
| early | short_turn | 307 | -249.48 | 520.36 | 97 |
| early | short_trend | 340 | -398.59 | 654.02 | 98 |
| early | combo_rebound_turn | 50 | -29.04 | 38.86 | 20 |
| early | combo_breakout_trend | 97 | -232.75 | 242.34 | 21 |
| late | baseline | 606 | -584.92 | 587.60 | 206 |
| late | risk | 604 | -575.03 | 577.66 | 205 |
| late | expected | 604 | -573.94 | 579.12 | 203 |
| late | expected/ATR | 604 | -573.51 | 578.78 | 205 |
| late | long_rebound | 349 | -176.61 | 511.58 | 135 |
| late | long_breakout | 344 | -366.63 | 595.26 | 127 |
| late | short_turn | 366 | -336.74 | 343.53 | 116 |
| late | short_trend | 346 | -364.67 | 382.12 | 97 |
| late | combo_rebound_turn | 111 | +67.27 | 88.81 | 46 |
| late | combo_breakout_trend | 86 | -147.02 | 159.31 | 19 |

## Binance 48h, 520 usable, 6188 incomplete paths

| period | rule | trades | net USD | max drawdown USD | wins |
|---|---|---:|---:|---:|---:|
| all | baseline | 128 | -70.19 | 100.32 | 58 |
| all | risk | 128 | -70.19 | 100.32 | 58 |
| all | expected | 128 | -92.06 | 108.16 | 57 |
| all | expected/ATR | 128 | -86.85 | 111.25 | 57 |
| all | long_rebound | 73 | -112.71 | 247.04 | 26 |
| all | long_breakout | 77 | -198.58 | 288.33 | 22 |
| all | short_turn | 64 | +70.36 | 152.25 | 39 |
| all | short_trend | 83 | -69.73 | 191.21 | 39 |
| all | combo_rebound_turn | 9 | +27.85 | 26.54 | 7 |
| all | combo_breakout_trend | 32 | -190.28 | 234.07 | 3 |
| early | baseline | 128 | -70.19 | 100.32 | 58 |
| early | risk | 128 | -70.19 | 100.32 | 58 |
| early | expected | 128 | -92.06 | 108.16 | 57 |
| early | expected/ATR | 128 | -86.85 | 111.25 | 57 |
| early | long_rebound | 73 | -112.71 | 247.04 | 26 |
| early | long_breakout | 77 | -198.58 | 288.33 | 22 |
| early | short_turn | 64 | +70.36 | 152.25 | 39 |
| early | short_trend | 83 | -69.73 | 191.21 | 39 |
| early | combo_rebound_turn | 9 | +27.85 | 26.54 | 7 |
| early | combo_breakout_trend | 32 | -190.28 | 234.07 | 3 |
| late | baseline | 0 | +0.00 | 0.00 | 0 |
| late | risk | 0 | +0.00 | 0.00 | 0 |
| late | expected | 0 | +0.00 | 0.00 | 0 |
| late | expected/ATR | 0 | +0.00 | 0.00 | 0 |
| late | long_rebound | 0 | +0.00 | 0.00 | 0 |
| late | long_breakout | 0 | +0.00 | 0.00 | 0 |
| late | short_turn | 0 | +0.00 | 0.00 | 0 |
| late | short_trend | 0 | +0.00 | 0.00 | 0 |
| late | combo_rebound_turn | 0 | +0.00 | 0.00 | 0 |
| late | combo_breakout_trend | 0 | +0.00 | 0.00 | 0 |

## Bithumb 12h, 364 usable, 211 incomplete paths

| period | rule | trades | net USD | max drawdown USD | wins |
|---|---|---:|---:|---:|---:|
| all | baseline | 360 | -494.51 | 502.90 | 93 |
| all | risk | 337 | -469.34 | 470.19 | 92 |
| all | expected | 337 | -469.34 | 470.19 | 92 |
| all | expected/ATR | 337 | -469.34 | 470.19 | 92 |
| all | long_rebound | 178 | -320.75 | 329.12 | 47 |
| all | long_breakout | 187 | -352.01 | 358.69 | 47 |
| all | short_turn | 195 | -187.88 | 235.30 | 58 |
| all | short_trend | 220 | -286.60 | 309.56 | 57 |
| all | combo_rebound_turn | 36 | -39.29 | 43.20 | 13 |
| all | combo_breakout_trend | 70 | -169.27 | 169.27 | 12 |
| early | baseline | 265 | -357.50 | 364.09 | 70 |
| early | risk | 247 | -332.66 | 339.24 | 70 |
| early | expected | 247 | -332.66 | 339.24 | 70 |
| early | expected/ATR | 247 | -332.66 | 339.24 | 70 |
| early | long_rebound | 130 | -248.23 | 250.31 | 35 |
| early | long_breakout | 133 | -269.05 | 271.00 | 35 |
| early | short_turn | 144 | -103.74 | 228.25 | 47 |
| early | short_trend | 165 | -192.85 | 242.38 | 46 |
| early | combo_rebound_turn | 27 | -19.32 | 23.70 | 12 |
| early | combo_breakout_trend | 51 | -129.25 | 135.73 | 11 |
| late | baseline | 95 | -137.01 | 145.40 | 23 |
| late | risk | 90 | -136.68 | 137.54 | 22 |
| late | expected | 90 | -136.68 | 137.54 | 22 |
| late | expected/ATR | 90 | -136.68 | 137.54 | 22 |
| late | long_rebound | 48 | -72.52 | 80.89 | 12 |
| late | long_breakout | 54 | -82.96 | 89.64 | 12 |
| late | short_turn | 51 | -84.14 | 84.14 | 11 |
| late | short_trend | 55 | -93.75 | 93.75 | 11 |
| late | combo_rebound_turn | 9 | -19.97 | 20.20 | 1 |
| late | combo_breakout_trend | 19 | -40.02 | 40.02 | 1 |

## Bithumb 48h, 110 usable, 474 incomplete paths

| period | rule | trades | net USD | max drawdown USD | wins |
|---|---|---:|---:|---:|---:|
| all | baseline | 110 | -145.21 | 147.91 | 36 |
| all | risk | 99 | -128.44 | 133.01 | 36 |
| all | expected | 99 | -128.44 | 133.01 | 36 |
| all | expected/ATR | 99 | -128.44 | 133.01 | 36 |
| all | long_rebound | 52 | -159.92 | 182.14 | 13 |
| all | long_breakout | 54 | -167.06 | 187.61 | 13 |
| all | short_turn | 53 | +17.43 | 229.02 | 26 |
| all | short_trend | 70 | -81.96 | 194.69 | 26 |
| all | combo_rebound_turn | 6 | -14.05 | 15.93 | 3 |
| all | combo_breakout_trend | 25 | -120.58 | 126.74 | 3 |
| early | baseline | 110 | -145.21 | 147.91 | 36 |
| early | risk | 99 | -128.44 | 133.01 | 36 |
| early | expected | 99 | -128.44 | 133.01 | 36 |
| early | expected/ATR | 99 | -128.44 | 133.01 | 36 |
| early | long_rebound | 52 | -159.92 | 182.14 | 13 |
| early | long_breakout | 54 | -167.06 | 187.61 | 13 |
| early | short_turn | 53 | +17.43 | 229.02 | 26 |
| early | short_trend | 70 | -81.96 | 194.69 | 26 |
| early | combo_rebound_turn | 6 | -14.05 | 15.93 | 3 |
| early | combo_breakout_trend | 25 | -120.58 | 126.74 | 3 |
| late | baseline | 0 | +0.00 | 0.00 | 0 |
| late | risk | 0 | +0.00 | 0.00 | 0 |
| late | expected | 0 | +0.00 | 0.00 | 0 |
| late | expected/ATR | 0 | +0.00 | 0.00 | 0 |
| late | long_rebound | 0 | +0.00 | 0.00 | 0 |
| late | long_breakout | 0 | +0.00 | 0.00 | 0 |
| late | short_turn | 0 | +0.00 | 0.00 | 0 |
| late | short_trend | 0 | +0.00 | 0.00 | 0 |
| late | combo_rebound_turn | 0 | +0.00 | 0.00 | 0 |
| late | combo_breakout_trend | 0 | +0.00 | 0.00 | 0 |
