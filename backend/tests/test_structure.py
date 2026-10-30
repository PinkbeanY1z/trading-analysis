from app.services.structure_service import structure,pivots
from app.services.indicator_service import calculate
import pytest

def rows(n=250): return [dict(t=str(i),o=10,c=10,h=11,l=9,v=None) for i in range(n)]
@pytest.mark.parametrize('n',[0,10,250])
def test_flat_missing(n):
    data=rows(n); result=structure(data,calculate(data))
    assert result['volume_breakout'] is None
    assert result['local_highs']==[] and result['rsi_divergence']==[]
    assert result['confirmation'].startswith('provisional')
    assert result['rsi_state']==('neutral' if n>=15 else 'unavailable')

def test_pivot_bearish_divergence():
    data=rows(30); data[20]['h']=15; data[25]['h']=16
    indicators=calculate(data); indicators['rsi'][20]=75; indicators['rsi'][25]=65
    result=structure(data,indicators)
    assert result['rsi_divergence'][0]['type']=='bearish'
    assert result['rsi_divergence'][0]['indices']==[20,25]

def test_pivot_bullish_divergence():
    data=rows(30); data[20]['l']=7; data[25]['l']=6
    indicators=calculate(data); indicators['rsi'][20]=25; indicators['rsi'][25]=35
    assert structure(data,indicators)['rsi_divergence'][0]['type']=='bullish'

@pytest.mark.parametrize('rsi,state',[(0,'oversold'),(30,'oversold'),(70,'overbought'),(100,'overbought')])
def test_rsi_boundaries(rsi,state):
    data=rows(); i=calculate(data); i['latest']['rsi']=rsi
    assert structure(data,i)['rsi_state']==state
