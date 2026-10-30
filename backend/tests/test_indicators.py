from app.services.indicator_service import sma, rsi, atr, calculate
import pytest

@pytest.mark.parametrize('period',[7,20,25,50,60,200])
def test_sma(period):
    result=sma(list(range(1,301)),period)
    assert result[period-2] is None
    assert result[period-1] == (period+1)/2
    assert result[-1] == (601-period)/2

@pytest.mark.parametrize('values,expected',[(list(range(30)),100),(list(range(30,0,-1)),0),([10]*30,50)])
def test_rsi_edges(values,expected):
    result=rsi(values)
    assert len(result)==len(values)
    assert result[:14]==[None]*14
    assert result[-1]==expected

def test_wilder_recurrence():
    values=list(range(15))+[12]
    assert rsi(values)[-1] == pytest.approx(100-100/(1+13/2))

def test_atr():
    candles=[{'h':12,'l':8,'c':10} for _ in range(40)]
    values=atr(candles)
    assert values[:14]==[None]*14
    assert values[14:]==[4]*26

def test_short_missing():
    rows=[{'h':12,'l':8,'c':10,'v':None} for _ in range(20)]
    result=calculate(rows)
    assert result['latest']['ma200'] is None
    assert result['latest']['maAlignment']=='unknown'
    assert result['latest']['relativeVolume'] is None
    assert result['insufficient_history']

def test_gold_volume():
    rows=[{'h':12,'l':8,'c':10,'v':999} for _ in range(250)]
    assert calculate(rows,'gold')['latest']['relativeVolume'] is None

def test_empty():
    assert calculate([])['latest']['close'] is None
