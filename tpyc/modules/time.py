"""
TurboPython time module.

Provides time-related functions matching Python's time module.
"""

from tpyc.modules import BuiltinModule, MethodDef, ParamDef
from tpyc.typesys import FLOAT, VOID

module = BuiltinModule("time")

# time.time() returns seconds since epoch as float (matching Python)
module.function("time", overloads=[
    MethodDef(
        params=[],
        returns=FLOAT,
        cpp="tpy::time_time()",
    ),
])

# time.sleep(seconds) - suspend execution for given number of seconds
module.function("sleep", overloads=[
    MethodDef(
        params=[ParamDef("seconds", FLOAT)],
        returns=VOID,
        cpp="tpy::time_sleep({0})",
    ),
])
