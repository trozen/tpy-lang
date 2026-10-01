# A float literal stored into a float32 local converts into it, as an int
# literal converts into an int16 one: a local whose first binding is a
# float32 value keeps that type.
from tpy import float32


def f32() -> float32:
    return float32(1.5)


# the constructor first binding gives the local float32: the later literal
# converts
def declared() -> None:
    f = float32(0.1)  # tpyc: type(float32)
    print("declared", f * 3)
    # the float literal stored into the float32 local; 0.5 is exact in
    # float32, so the output matches CPython (float/float32_declared_literal_rounds
    # stores one that rounds)
    f = 0.5  # tpyc: ok
    print("declared", f * 3)


# a float32 local a nested def stores a float literal into through `nonlocal`
# (a name no pending local can be): the literal converts
def nonlocal_store() -> None:
    v = f32()  # tpyc: type(float32)

    def reset() -> None:
        nonlocal v
        # the float literal stored into the float32 local
        v = 0.25  # tpyc: ok
    reset()
    print("nonlocal", v * 2)


# an infinite literal fits a float32
def infinite() -> None:
    f = float32(1.5)  # tpyc: type(float32)
    f = 1e400  # tpyc: ok
    print("infinite", f)


declared()
nonlocal_store()
infinite()
