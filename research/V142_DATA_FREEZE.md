# v1.4.2 Immutable Data Freeze

The v1.4.2 discovery dataset was frozen before any strategy return was calculated.

- Workflow run: `30516539776`
- Artifact: `crypto-multiregime-v142-data-freeze`
- Artifact ID: `8749247737`
- Artifact digest: `sha256:491a9bc1e9b3213dfd39a6de39ab36a7e9d91dc7f6381eea695d80935f4526b4`
- Frozen protocol commit: `6dc91831b3f5278a062b4b4ab669a48dbe58cbdc`
- Price dataset fingerprint: `03e19a89132171265cc40707c343a89b36766c7c811d7ae1eba03a9599b47820`
- Price manifest SHA-256: `cd862f6ad739921663d97b5eb5424b17ce6af2d07abd40a0521ba47c2b9e27eb`
- External manifest SHA-256: `cae89da7cc39353a51eba72cd14f1c66eeff88d059c32283d4248694e3bfa5ae`
- Market interval: `2023-11-15T00:00:00` inclusive to `2025-11-23T00:00:00` exclusive
- Four-hour candles per asset: `4,434`
- Completed hourly candles per asset: `17,736`
- Audited bounded-continuity candles: `43`
- Assets: APT, ARB, AVAX, DOT, FIL, NEAR, OP and SUI

The artifact contains raw hourly Coinbase candles, completed hourly candles, four-hour candles, Hyperliquid settled funding, Coin Metrics stablecoin data, FRED macro data, source manifests and hashes.

The freeze workflow explicitly recorded:

- `strategy_returns_calculated: false`
- `holdout_returns_calculated: false`
- `paper_only: true`
- `authorizes_real_trading: false`

Discovery must download this exact artifact from the recorded workflow run and verify all committed identifiers before evaluation. Refetching or substituting data is forbidden. The final 720-bar holdout remains locked.
