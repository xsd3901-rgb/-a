from __future__ import annotations

from eastmoney_formula import build_formula


def main() -> None:
    formula = build_formula()
    assert "QSCORE" in formula
    assert "XG:" in formula
    assert "ATRP" in formula
    assert "MA(AMOUNT,5)" in formula
    assert "P1:=IF(RSI6>85,12,0)" in formula
    assert "S7:=IF(VR5>=1.05 AND VR5<=3.5,12,0)" in formula
    print("OFFLINE_EASTMONEY_FORMULA_OK")


if __name__ == "__main__":
    main()
