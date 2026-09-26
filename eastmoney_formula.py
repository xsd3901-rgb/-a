from __future__ import annotations

from config import SETTINGS, ensure_directories
from profile import load_strategy_profile


def build_formula() -> str:
    threshold = int(load_strategy_profile()["score_threshold"])
    min_amount = int(SETTINGS.risk_min_amount_ma5)
    max_atr = float(SETTINGS.risk_max_atr_pct)

    return rf"""{{A-Quant V1 沪深A股日线波段参考公式}}
{{说明：对应本地 V1 主要评分与基础风险闸门；ST/停牌/历史交易制度仍以本地系统为准。}}

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

TR0:=MAX(MAX(HIGH-LOW,ABS(HIGH-REF(CLOSE,1))),ABS(LOW-REF(CLOSE,1)));
ATR14:=MA(TR0,14);
ATRP:=ATR14/CLOSE*100;

VR5:=VOL/MA(VOL,5);
RET5:=(CLOSE/REF(CLOSE,5)-1)*100;
RET20:=(CLOSE/REF(CLOSE,20)-1)*100;
POS20:=CLOSE/HHV(HIGH,20);

S1:=IF(CLOSE>M20,12,0);
S2:=IF(M5>M10 AND M10>M20,14,0);
S3:=IF(M20>M60,8,0);
S4:=IF(DIF>DEA,12,0);
S5:=IF(RSI6>=45 AND RSI6<=78,10,0);
S6:=IF(K>D AND J<100,8,0);
S7:=IF(VR5>=1.05 AND VR5<=3.5,12,0);
S8:=IF(POS20>=0.94,10,0);
S9:=IF(RET5>=0 AND RET5<=18,7,0);
S10:=IF(RET20>=-5 AND RET20<=35,7,0);

P1:=IF(RSI6>85,12,0);
P2:=IF(ATRP>9,10,0);
P3:=IF(RET5>25,10,0);

QSCORE:=MAX(0,MIN(100,S1+S2+S3+S4+S5+S6+S7+S8+S9+S10-P1-P2-P3));

LIQOK:=MA(AMOUNT,5)>={min_amount};
VOLOK:=ATRP<={max_atr:g};

XG:QSCORE>={threshold} AND LIQOK AND VOLOK;
"""


FORMULA = build_formula()


def write_formula() -> str:
    ensure_directories()
    path = SETTINGS.report_dir / "eastmoney_formula_v1.txt"
    path.write_text(build_formula(), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(write_formula())
