# @readonly: writing to value-type globals is allowed (readonly protects params, not globals).
from tpy import int32, readonly


x: int32 = 0


@readonly
def ok() -> int32:
    global x
    x = 1  # tpyc: ok
    return x


print(ok())
