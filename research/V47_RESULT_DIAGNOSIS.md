# v4.7 Macro-Risk Confirmation — Result Diagnosis

## Verdict

v4.7 completed successfully, including independent FRED source validation, six walk-forward base-model refits, standard/stress evaluation of 37 pre-registered policies, sealed evaluation and evidence upload.

No active macro policy was eligible. The exact v4.4 baseline was selected.

## Reproduced result

- aggregate standard return: `0.0309676899827247`
- aggregate stress return: `0.027664740858863945`
- annualized standard return: `0.042901252067619966`
- maximum drawdown: `0.01054364964009935`
- standard return change versus v4.4: `0.0`
- report SHA-256: `6ba2ff9a18acf065dec7c507da8851e146e6b68c566200740d1ee6b74e9121b1`

## Walk-forward diagnosis

All 36 active policies failed all four core robustness conditions:

- fewer than four positive standard-excess folds
- non-positive compounded standard excess
- non-positive compounded stress excess
- worst standard fold excess below -0.30%

Across the active grid:

- positive standard-excess folds ranged from one to two of six
- best compounded standard excess was approximately `-0.699%`
- best compounded stress excess was approximately `-0.660%`
- best worst-fold standard excess was approximately `-0.719%`

The macro source was valid and lag-safe, but broad supportive/defensive exposure scaling was not sufficiently predictive for this strategy.

## Interpretation

This negative result rules out another broad market-risk sizing layer built from VIX, the broad dollar and real yields. It does not imply that independent data are useless; it shows that coarse portfolio-level resizing loses information.

The next experiment should inject independent cross-exchange price/liquidity confirmation directly into model features so the learned regime and ranking models can use the information conditionally rather than through a fixed scaler.

## Next experiment

v4.8 should use reproducible Coinbase BTC/USD and ETH/USD daily candles alongside Binance spot data to construct:

- cross-exchange momentum agreement
- Coinbase-minus-Binance return divergence
- USD-versus-USDT premium level and change
- Coinbase/Binance volume-share and liquidity acceleration

Feature families must be selected only through the six walk-forward validations before a final augmented bundle is trained and applied to the exposed sealed windows.

Paper-only. No live-trading authorization.
