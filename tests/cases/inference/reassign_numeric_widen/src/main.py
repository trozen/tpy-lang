# Numeric widening across reassignments: integers widen to the wider integer and
# floats to the wider float; an int and a float never join (a local has one numeric
# type -- inference/error_rebind_int_float), except through a declared float slot.
from typing import Callable
from tpy import int32, int64, uint8, uint32, float32, Own

def test_int_widen() -> None:
    a = int32(1)  # tpyc: type(int64)
    a = int64(2)  # tpyc: type(int64)
    print("int_widen", a)

def test_float_widen() -> None:
    b = float32(1.5)  # tpyc: type(float)
    b = 2.5  # tpyc: type(float)
    print("float_widen", b)

def test_float_stays_float() -> None:
    c = 1.5  # tpyc: type(float)
    c = float32(1.0)  # tpyc: type(float)
    print("float_stays", c)

def test_bigint_absorbs_fixedint() -> None:
    d = int32(1)  # tpyc: type(int)
    d = int(2)  # tpyc: type(int)
    print("bigint", d)

def test_unsigned_to_wider_signed() -> None:
    e = uint8(1)  # tpyc: type(int32)
    e = int32(2)  # tpyc: type(int32)
    print("unsigned", e)

def test_uint32_to_int64() -> None:
    g = uint32(1)  # tpyc: type(int64)
    g = int64(2)  # tpyc: type(int64)
    print("uint32", g)

def test_branch_int_widen(c: bool) -> None:
    # int32 and int64 bindings in two arms still join to the wider integer
    h = int32(1)  # tpyc: type(int64)
    if c:
        h = int64(7)  # tpyc: ok
    print("branch_int", h)

def test_declared_float_local(c: bool) -> None:
    # a declared float slot converts an int value (the numeric tower); the
    # converted int is never printed, since TPy shows 0.0 where CPython shows 0
    x: float = 0
    if c:
        x = 2.5  # tpyc: ok
    print("declared_local", x)

def test_declared_float_param(t: float) -> None:
    # a float parameter is a declared slot too: the int converts
    t = 0  # tpyc: ok
    print("declared_param", t + 0.5)

def test_declared_float_nonlocal(t: float) -> None:
    # a closure's nonlocal write reaches the enclosing function's declared float
    def reset() -> None:
        nonlocal t
        t = 0  # tpyc: ok
    reset()
    print("declared_nonlocal", t + 0.5)

def test_declared_float_nonlocal_local() -> None:
    # the enclosing function's annotated local is a declared slot too
    t: float = 1.5
    def reset() -> None:
        nonlocal t
        t = 0  # tpyc: ok
    reset()
    print("declared_nonlocal_local", t + 0.5)

def pair() -> tuple[int32, int32]:
    return (3, 4)

def test_declared_float_unpack() -> None:
    # a tuple unpack into a declared float converts the int element
    x: float = 2.5
    x, y = pair()  # tpyc: ok
    print("declared_unpack", x + 0.5, y)

G: float = 1.5

def test_declared_float_global() -> None:
    # a `global` write reaches the module's declared float
    global G
    G = 0  # tpyc: ok
    print("declared_global", G + 0.5)

# The local's own type still hints its reassignment's value, for what the
# hint does besides numbers.
def test_hint_float32_literal() -> None:
    # a float literal narrows to the float32 elements of the local it rebinds
    e = {"a": float32(0.25)}
    e = {"k": 0.5}  # tpyc: ok
    print("hint_float32", e)

def test_hint_lambda() -> None:
    # a lambda rebound over a callable local takes its parameter types
    h: Callable[[float], float] = lambda x: x
    k = h
    k = lambda x: x + 1.0  # tpyc: ok
    print("hint_lambda", k(1.0))

def empty_list[T](n: int) -> Own[list[T]]:
    out: list[T] = []
    return out

def test_hint_generic_call(xs: list[float]) -> None:
    # a generic call infers its T from the local it rebinds
    ys = xs
    ys = empty_list(3)  # tpyc: ok
    ys.append(2.5)
    print("hint_generic", ys, xs)

def make_groups() -> Own[dict[str, list[float]]]:
    return {"a": [1.5]}

def test_hint_nested_empty() -> None:
    # a nested [] takes its element type from the local it rebinds
    d = make_groups()
    d = {"b": []}  # tpyc: ok
    d["b"].append(2.5)
    print("hint_nested_empty", d)

def test_float_seed(c: bool) -> None:
    # 0.0 is the spelling that makes CPython and TPy agree on the type
    total = 0.0
    for i in range(3):
        total += 0.5  # tpyc: ok
    if c:
        total = 9.5  # tpyc: ok
    print("float_seed", total)

def test_captured_none_seed() -> None:
    # a nested def read the None-seeded local while it was None; the value
    # binding still makes it `int32 | None`, which the def reads as before
    x = None
    def g() -> None:
        print("captured_none_seed", x)
    g()
    x = 5  # tpyc: ok
    g()

test_int_widen()
test_float_widen()
test_float_stays_float()
test_bigint_absorbs_fixedint()
test_unsigned_to_wider_signed()
test_uint32_to_int64()
test_branch_int_widen(True)
test_declared_float_local(True)
test_declared_float_param(1.5)
test_declared_float_nonlocal(2.5)
test_declared_float_nonlocal_local()
test_declared_float_unpack()
test_declared_float_global()
test_hint_float32_literal()
test_hint_lambda()
test_hint_generic_call([1.5])
test_hint_nested_empty()
test_float_seed(False)
test_captured_none_seed()
