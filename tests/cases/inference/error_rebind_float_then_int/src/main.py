# A float local rebound to a typed int value is refused, with a float(...) conversion as the fix.
from tpy import int32

def pick(c: bool, n: int32) -> None:
    x = 2.5
    if c:
        x = n  # tpyc: error(/'x' is bound to float at line 5 and to int32 here.*x = float\(n\), or annotate x: float/)
    print(x)

pick(True, 3)
