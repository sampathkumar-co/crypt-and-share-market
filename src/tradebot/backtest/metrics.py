def max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0] if equity_curve else 0; worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak: worst = min(worst, (value - peak) / peak)
    return abs(worst)

def win_rate(pnls: list[float]) -> float:
    return sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0
