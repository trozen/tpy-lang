"""
TurboPython time module.

Provides time-related functions matching Python's time module.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import BIGINT

module = BuiltinModule("time")

# time.time() returns seconds since epoch as int (BigInt)
# Using int instead of float to avoid introducing float type
module.function("time", overloads=[
    MethodDef(
        params=[],
        returns=BIGINT,
        cpp="tpy::time_time()",
    ),
])
