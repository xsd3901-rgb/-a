from __future__ import annotations

from config import SETTINGS, ensure_directories


FORMULA = r"""{短期波段量化选股器 - 东方财富参考公式}
M5:=MA(CLOSE,5);
M10:=MA(CLOSE,10);
M20:=MA(CLOSE,20);
M60:=MA(CLOSE,60);
DIF:=EMA(CLOSE,12)-EMA(CLOSE,26);
DEA:=EMA(DIF,9);
RSV:=(CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;
K:=SMA(RSV,3,1);
D:=SMA(K,3,1);
J:=3*K-2*D;
LC:=REF(CLOSE,1);
RSI6:=SMA(MAX(CLOSE-LC,0),6,1)/SMA(ABS(CLOSE-LC),6,1)*100;
VR5:=VOL/MA(VOL,5);
H20:=HHV(HIGH,20);

TREND:=CLOSE>M20 AND M5>M10 AND M10>M20;
MOM:=DIF>DEA AND RSI6>=45 AND RSI6<=78 AND K>D AND J<100;
VOLUME:=VR5>=1.05 AND VR5<=3.5;
STRONG:=CLOSE/H20>=0.94;

XG:TREND AND MOM AND VOLUME AND STRONG;
"""


def write_formula() -> str:
    ensure_directories()
    path = SETTINGS.report_dir / "eastmoney_formula.txt"
    path.write_text(FORMULA, encoding="utf-8")
    return str(path)
