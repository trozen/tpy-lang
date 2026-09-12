# Plain `int` is BigInt, which has no C spelling. `def f(n: int)` is what a
# Python programmer writes by reflex, so this is the highest-traffic rejection
# of the C-ABI gate; the hint has to name the fixed-width replacement.
from tpy.extern import export
from tpy import int32

# The supported spelling, kept next to the rejected one for contrast.
@export(binding="C")
def tick_ok(n: int32) -> None:
    print(n)

@export(binding="C")
def tick_bad(n: int) -> None:  # tpyc: error(/parameter 'n': type 'int' is not representable in the C ABI; use a fixed-width integer type \(int32, int64, ...\)/)
    print(n)
