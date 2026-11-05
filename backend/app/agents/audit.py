"""Compact execution fingerprints: retain audit consistency without duplicate payloads."""
import hashlib
import json


def evidence_digest(evidence):
    fields=('symbol','asset_class','interval','source','last_updated','snapshot_id',
            'technical_indicators','market_structure','support_resistance','analysis',
            'trade_plan','calculation','calculation_id')
    payload={field:evidence[field] for field in fields if field in evidence}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
