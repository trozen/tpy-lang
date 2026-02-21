# @readonly: writing to value-type globals is allowed (readonly protects params, not globals).
from tpy import Int32, readonly


x: Int32 = 0


@readonly
def ok() -> Int32:
    global x
    x = 1  # tpyc: ok
    return x


print(ok())
