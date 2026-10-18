# A caller that imports the constants the defaults name: the fill keeps
# the spelling, the read of the imported name.
from tpy import int32, int64
from defs import f, ft, fw, fx, fm, C, K, Made, PAIR, Pt
import consts


def run() -> None:
    print("matching f", f(1, c=9), K)  # tpyc: ok
    # A Final tuple default fills from a caller naming the same tuple.
    print("matching tuple", ft(c=9), PAIR[1])  # tpyc: ok
    # Dotted defaults the caller spells the same way (`C.X`, `consts.Q`).
    print("matching dotted", fx(c=9), fm(c=9), C.X, consts.Q)  # tpyc: ok
    print("matching type param", TG[str]().run(), tg(1.5), K)
    # A constructor callee the caller names the same class by.
    print("matching int ctor", fw(c=9), int64(1))  # tpyc: ok
    m = Made(c=9)  # tpyc: ok
    m.p.x += 1
    print("matching factory", m.p.x, len(m.ys), m.c, Pt().x)


# A type parameter named like the default's constant is not it.
class TG[K]:
    def __init__(self) -> None:
        pass

    def run(self) -> int32:
        return f(1, c=9)  # tpyc: ok


def tg[K](v: K) -> int32:
    return f(1, c=9)  # tpyc: ok
