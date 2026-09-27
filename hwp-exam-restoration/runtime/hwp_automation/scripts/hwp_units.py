"""Unit conversions shared by authoring and layout APIs."""
import math
def _finite(value):
    number=float(value)
    if not math.isfinite(number): raise ValueError('Measurement must be finite')
    return number
def mm_to_hwp(value): return round(_finite(value)*7200/25.4)
def pt_to_hwp(value): return round(_finite(value)*100)
def hwp_to_mm(value): return _finite(value)*25.4/7200
def hwp_to_pt(value): return _finite(value)/100
