# Checked math calls preserve Python exceptions and IEEE results in every body.
# Scalar boundaries use value semantics; no reference result crosses a call.
import asyncio
import math
from math import sqrt
from typing import Iterator

from tpy import ReturnException, error_return, nocopy


def unary(name: str, x: float) -> float:
    # Free function: every unary export receives a scalar parameter once.
    if name == "sqrt":
        return sqrt(x)  # tpyc: ok
    if name == "log":
        return math.log(x)  # tpyc: ok
    if name == "log10":
        return math.log10(x)  # tpyc: ok
    if name == "log2":
        return math.log2(x)  # tpyc: ok
    if name == "log1p":
        return math.log1p(x)  # tpyc: ok
    if name == "asin":
        return math.asin(x)  # tpyc: ok
    if name == "acos":
        return math.acos(x)  # tpyc: ok
    if name == "acosh":
        return math.acosh(x)  # tpyc: ok
    if name == "atanh":
        return math.atanh(x)  # tpyc: ok
    if name == "sin":
        return math.sin(x)  # tpyc: ok
    if name == "cos":
        return math.cos(x)  # tpyc: ok
    if name == "tan":
        return math.tan(x)  # tpyc: ok
    if name == "exp":
        return math.exp(x)  # tpyc: ok
    if name == "exp2":
        return math.exp2(x)  # tpyc: ok
    if name == "expm1":
        return math.expm1(x)  # tpyc: ok
    if name == "sinh":
        return math.sinh(x)  # tpyc: ok
    if name == "cosh":
        return math.cosh(x)  # tpyc: ok
    if name == "gamma":
        return math.gamma(x)  # tpyc: ok
    if name == "lgamma":
        return math.lgamma(x)  # tpyc: ok
    raise AssertionError("unknown unary operation")


def category(x: float) -> str:
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "-inf" if x < 0.0 else "+inf"
    if x == 0.0:
        return "-zero" if math.copysign(1.0, x) < 0.0 else "+zero"
    return "finite"


def check_unary(name: str, x: float, expected: str) -> None:
    actual = "missing"
    try:
        actual = category(unary(name, x))
    except ValueError:
        actual = "domain"
    except OverflowError:
        actual = "overflow"
    assert actual == expected, name


def unary_policy() -> None:
    # Free function: domain/pole/range errors and their nonfinite inverses.
    for name, x, expected in [
        ("sqrt", -1.0, "domain"), ("sqrt", -math.inf, "domain"),
        ("log", -1.0, "domain"), ("log", 0.0, "domain"),
        ("log", -0.0, "domain"), ("log", -math.inf, "domain"),
        ("log10", -1.0, "domain"), ("log10", 0.0, "domain"),
        ("log10", -math.inf, "domain"),
        ("log2", -1.0, "domain"), ("log2", 0.0, "domain"),
        ("log2", -math.inf, "domain"),
        ("log1p", -1.0, "domain"), ("log1p", -2.0, "domain"),
        ("log1p", -math.inf, "domain"),
        ("asin", -2.0, "domain"), ("asin", 2.0, "domain"),
        ("asin", math.inf, "domain"), ("asin", -math.inf, "domain"),
        ("acos", -2.0, "domain"), ("acos", 2.0, "domain"),
        ("acos", math.inf, "domain"), ("acos", -math.inf, "domain"),
        ("acosh", 0.0, "domain"), ("acosh", -math.inf, "domain"),
        ("atanh", -1.0, "domain"), ("atanh", 1.0, "domain"),
        ("atanh", -2.0, "domain"), ("atanh", 2.0, "domain"),
        ("atanh", math.inf, "domain"), ("atanh", -math.inf, "domain"),
        ("sin", math.inf, "domain"), ("sin", -math.inf, "domain"),
        ("cos", math.inf, "domain"), ("cos", -math.inf, "domain"),
        ("tan", math.inf, "domain"), ("tan", -math.inf, "domain"),
        ("exp", 1000.0, "overflow"), ("exp2", 1024.0, "overflow"),
        ("expm1", 1000.0, "overflow"),
        ("sinh", 1000.0, "overflow"), ("sinh", -1000.0, "overflow"),
        ("cosh", 1000.0, "overflow"), ("cosh", -1000.0, "overflow"),
        ("gamma", 0.0, "domain"), ("gamma", -0.0, "domain"),
        ("gamma", -1.0, "domain"), ("gamma", -2.0, "domain"),
        ("gamma", -math.inf, "domain"), ("gamma", 172.0, "overflow"),
        ("gamma", 5e-324, "overflow"), ("gamma", -5e-324, "overflow"),
        ("lgamma", 0.0, "domain"), ("lgamma", -0.0, "domain"),
        ("lgamma", -1.0, "domain"), ("lgamma", -2.0, "domain"),
        ("lgamma", 1e308, "overflow"),
        ("sqrt", math.inf, "+inf"), ("log", math.inf, "+inf"),
        ("log10", math.inf, "+inf"), ("log2", math.inf, "+inf"),
        ("log1p", math.inf, "+inf"), ("acosh", math.inf, "+inf"),
        ("exp", math.inf, "+inf"), ("exp", -math.inf, "+zero"),
        ("exp", -1000.0, "+zero"),
        ("exp2", math.inf, "+inf"), ("exp2", -math.inf, "+zero"),
        ("exp2", -1075.0, "+zero"),
        ("expm1", math.inf, "+inf"), ("expm1", -math.inf, "finite"),
        ("expm1", -1000.0, "finite"),
        ("sinh", math.inf, "+inf"), ("sinh", -math.inf, "-inf"),
        ("cosh", math.inf, "+inf"), ("cosh", -math.inf, "+inf"),
        ("gamma", math.inf, "+inf"), ("gamma", -200.5, "-zero"),
        ("lgamma", math.inf, "+inf"), ("lgamma", -math.inf, "+inf"),
        ("sqrt", -0.0, "-zero"), ("sqrt", 0.0, "+zero"),
        ("log1p", -0.0, "-zero"), ("expm1", -0.0, "-zero"),
        ("sin", -0.0, "-zero"), ("tan", -0.0, "-zero"),
        ("asin", -0.0, "-zero"), ("atanh", -0.0, "-zero"),
        ("sinh", -0.0, "-zero"),
    ]:
        check_unary(name, x, expected)
    print("free function: unary domains, overflow, infinities, signed zero")

    for name in ["sqrt", "log", "log10", "log2", "log1p", "asin", "acos",
                 "acosh", "atanh", "sin", "cos", "tan", "exp", "exp2",
                 "expm1", "sinh", "cosh", "gamma", "lgamma"]:
        check_unary(name, math.nan, "nan")
    print("free function: every unary NaN propagates")

    # Exact identities or wide tolerances avoid host-specific libm last bits.
    assert sqrt(9) == 3.0  # tpyc: ok
    assert math.log(1.0) == 0.0  # tpyc: ok
    assert math.log10(100.0) == 2.0  # tpyc: ok
    assert math.log2(8.0) == 3.0  # tpyc: ok
    assert 0.69 < math.log1p(1.0) < 0.70  # tpyc: ok
    assert 1.57 < math.asin(1.0) < 1.58  # tpyc: ok
    assert -1.58 < math.asin(-1.0) < -1.57  # tpyc: ok
    assert math.acos(1.0) == 0.0  # tpyc: ok
    assert 3.14 < math.acos(-1.0) < 3.15  # tpyc: ok
    assert math.acosh(1.0) == 0.0  # tpyc: ok
    assert 0.54 < math.atanh(0.5) < 0.56  # tpyc: ok
    assert math.sin(0.0) == 0.0  # tpyc: ok
    assert math.cos(0.0) == 1.0  # tpyc: ok
    assert math.tan(0.0) == 0.0  # tpyc: ok
    assert math.exp(0.0) == 1.0  # tpyc: ok
    assert math.exp2(3.0) == 8.0  # tpyc: ok
    assert math.expm1(-math.inf) == -1.0  # tpyc: ok
    assert math.expm1(-1000.0) == -1.0  # tpyc: ok
    assert math.sinh(0.0) == 0.0  # tpyc: ok
    assert math.cosh(0.0) == 1.0  # tpyc: ok
    assert 23.99 < math.gamma(5.0) < 24.01  # tpyc: ok
    assert -3.55 < math.gamma(-0.5) < -3.54  # tpyc: ok
    assert math.lgamma(1.0) == 0.0  # tpyc: ok
    print("free function: ordinary values and accepted domain boundaries")


def binary(name: str, x: float, y: float) -> float:
    # Free function: binary adapters preserve both already-evaluated operands.
    if name == "pow":
        return math.pow(x, y)  # tpyc: ok
    if name == "fmod":
        return math.fmod(x, y)  # tpyc: ok
    if name == "remainder":
        return math.remainder(x, y)  # tpyc: ok
    return math.log(x, y)  # tpyc: ok


def binary_policy() -> None:
    for name, x, y, expected in [
        ("pow", -2.0, 0.5, "domain"), ("pow", 0.0, -1.0, "domain"),
        ("pow", -0.0, -3.0, "domain"),
        ("pow", 1e308, 2.0, "overflow"), ("pow", -1e308, 3.0, "overflow"),
        ("pow", math.nan, 0.0, "finite"), ("pow", 1.0, math.nan, "finite"),
        ("pow", -1.0, math.inf, "finite"), ("pow", -math.inf, 0.5, "+inf"),
        ("pow", -math.inf, -3.0, "-zero"),
        ("pow", -0.0, -math.inf, "+inf"), ("pow", 0.0, -math.inf, "+inf"),
        ("pow", -0.0, 3.0, "-zero"), ("pow", -1e-300, 3.0, "-zero"),
        ("pow", 1e-300, 2.0, "+zero"),
        ("pow", math.nan, 2.0, "nan"), ("pow", 2.0, math.nan, "nan"),
        ("fmod", 1.0, 0.0, "domain"), ("fmod", math.inf, 2.0, "domain"),
        ("fmod", -math.inf, 2.0, "domain"),
        ("fmod", math.nan, 0.0, "nan"), ("fmod", math.inf, math.nan, "nan"),
        ("fmod", 2.0, math.inf, "finite"), ("fmod", -0.0, 2.0, "-zero"),
        ("remainder", 1.0, 0.0, "domain"),
        ("remainder", math.inf, 2.0, "domain"),
        ("remainder", -math.inf, 2.0, "domain"),
        ("remainder", math.nan, 0.0, "nan"),
        ("remainder", math.inf, math.nan, "nan"),
        ("remainder", 2.0, math.inf, "finite"),
        ("remainder", -0.0, 2.0, "-zero"),
        ("log", -1.0, 2.0, "domain"), ("log", 2.0, -1.0, "domain"),
        ("log", 0.0, 2.0, "domain"), ("log", 2.0, 0.0, "domain"),
        ("log", -1.0, math.nan, "domain"),
        ("log", math.nan, -1.0, "domain"),
        ("log", 2.0, 1.0, "division"),
        ("log", math.nan, 2.0, "nan"), ("log", 2.0, math.nan, "nan"),
    ]:
        actual = "missing"
        try:
            actual = category(binary(name, x, y))
        except ValueError:
            actual = "domain"
        except OverflowError:
            actual = "overflow"
        except ZeroDivisionError:
            actual = "division"
        assert actual == expected, name
    assert math.pow(-2.0, 3.0) == -8.0  # tpyc: ok
    assert math.pow(math.nan, 0.0) == 1.0  # tpyc: ok
    assert math.pow(1.0, math.nan) == 1.0  # tpyc: ok
    assert math.pow(-1.0, math.inf) == 1.0  # tpyc: ok
    assert math.fmod(-7.0, 2.0) == -1.0  # tpyc: ok
    assert math.remainder(7.0, 2.0) == -1.0  # tpyc: ok
    assert math.fmod(2.0, math.inf) == 2.0  # tpyc: ok
    assert math.remainder(2.0, math.inf) == 2.0  # tpyc: ok
    assert math.log(8.0, 2.0) == 3.0  # tpyc: ok
    print("free function: binary domains, overflow, NaN precedence and inverses")

    for x, exponent, expected in [
        (1.0, 1024, "overflow"), (-1.0, 1024, "overflow"),
        (math.inf, 1024, "+inf"), (-math.inf, 1024, "-inf"),
        (math.nan, 1024, "nan"), (0.0, 1024, "+zero"),
        (-0.0, 1024, "-zero"), (1.0, -1075, "+zero"),
        (-1.0, -1075, "-zero"),
    ]:
        actual = "missing"
        try:
            actual = category(math.ldexp(x, exponent))  # tpyc: ok
        except OverflowError:
            actual = "overflow"
        assert actual == expected
    assert math.ldexp(1.5, 3) == 12.0  # tpyc: ok
    print("free function: ldexp finite overflow, underflow and nonfinite inputs")


def ulp_policy() -> None:
    maximum = 1.7976931348623157e308
    predecessor = math.nextafter(maximum, 0.0)
    spacing = maximum - predecessor
    assert math.ulp(maximum) == spacing  # tpyc: ok
    assert math.ulp(-maximum) == spacing  # tpyc: ok
    assert math.ulp(predecessor) == spacing  # tpyc: ok
    assert math.ulp(0.0) == 5e-324  # tpyc: ok
    assert math.ulp(-0.0) == 5e-324  # tpyc: ok
    assert math.ulp(5e-324) == 5e-324  # tpyc: ok
    assert math.ulp(math.inf) == math.inf  # tpyc: ok
    assert math.ulp(-math.inf) == math.inf  # tpyc: ok
    assert math.isnan(math.ulp(math.nan))  # tpyc: ok
    print("free function: ulp max-finite, predecessor, zeros, subnormal, nonfinite")


@nocopy
class Roots:
    value: float

    def __init__(self, x: float) -> None:
        # Constructor: checked scalar result in a member initializer.
        self.value = sqrt(x)  # tpyc: ok

    def get(self) -> float:
        # Method: checked scalar field read and scalar return.
        return math.sqrt(self.value)  # tpyc: ok


def values() -> Iterator[float]:
    # Generator: error occurs only after the first suspension resumes.
    yield sqrt(4.0)  # tpyc: ok
    yield sqrt(-1.0)  # tpyc: ok
    print("generator: unreachable")


async def async_root(x: float) -> float:
    # Async: the checked call stays after the await.
    await asyncio.sleep(0)
    return sqrt(x)  # tpyc: ok


async def async_position() -> None:
    assert await async_root(9.0) == 3.0
    try:
        await async_root(-1.0)
        print("async: unreachable")
    except ValueError:
        print("async: value then caught domain")


def closure_position(x: float) -> None:
    # Closure: captured scalar input reaches the same checked binding.
    def captured() -> float:
        return sqrt(x)  # tpyc: ok

    try:
        result = captured()
        assert result == 4.0
        print("closure: value")
    except ValueError:
        print("closure: caught domain")


@nocopy
class Unwind:
    def __enter__(self) -> int:
        print("context manager: enter")
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        assert exc_val is not None
        assert isinstance(exc_val, ValueError)
        print("context manager: exit ValueError")
        return False


class MarkerError(Exception, ReturnException):
    pass


@error_return(MarkerError)
def error_return_root(x: float) -> float:
    # @error_return: math keeps ordinary throw-tier exception semantics.
    return sqrt(x)  # tpyc: ok


def optional_root(x: float | None) -> float:
    if x is not None:
        return sqrt(x)  # tpyc: ok
    return 0.0


def union_root(x: float | str) -> float:
    if isinstance(x, float):
        return sqrt(x)  # tpyc: ok
    return 0.0


def generic_root[T](tag: T, x: float) -> float:
    return sqrt(x)  # tpyc: ok


def mono_root(tag: int, x: float) -> float:
    return sqrt(x)  # tpyc: ok


left_count = 0
right_count = 0


def left(x: float) -> float:
    global left_count
    left_count += 1
    return x


def right(x: float) -> float:
    global right_count
    right_count += 1
    return x


# Typed subjects avoid BUGS.md#match-int-literal-subject-rejected.
def match_position(selector: int) -> None:
    # Match arm: skipped arms must not evaluate the scalar input expression.
    try:
        match selector:  # tpyc: ok
            case 0:
                print("match arm: skipped")
            case 1:
                assert sqrt(left(4.0)) == 2.0  # tpyc: ok
                print("match arm: value")
            case _:
                sqrt(left(-1.0))  # tpyc: ok
                print("match arm: unreachable")
    except ValueError:
        print("match arm: caught domain")


def evaluation() -> None:
    global left_count, right_count
    left_count = 0
    right_count = 0
    # Independent counters pin exactly-once evaluation without order dependence.
    assert math.pow(left(2.0), right(3.0)) == 8.0  # tpyc: ok
    assert left_count == 1 and right_count == 1
    try:
        math.pow(left(-2.0), right(0.5))  # tpyc: ok
        print("evaluation: unreachable")
    except ValueError:
        assert left_count == 2 and right_count == 2
    assert sqrt(left(4.0)) == 2.0  # tpyc: ok
    assert left_count == 3
    try:
        sqrt(left(-1.0))  # tpyc: ok
        print("evaluation: unreachable unary")
    except ValueError:
        assert left_count == 4
    print("evaluation: unary and binary arguments evaluated once")

    left_count = 0
    for choose in [False, True]:
        # Both conditional arms and short-circuit operands remain lazy.
        result = sqrt(left(4.0)) if choose else 0.0  # tpyc: ok
        assert result == (2.0 if choose else 0.0)
        assert choose or sqrt(left(9.0)) == 3.0  # tpyc: ok
        assert not (choose and sqrt(left(0.0)) != 0.0)  # tpyc: ok
    assert left_count == 3
    print("evaluation: ternary and short-circuit placement")

    left_count = 0
    for selector in [0, 1, 2]:
        match_position(selector)
    assert left_count == 2


def positions() -> None:
    root = Roots(16.0)
    assert root.value == 4.0
    print("constructor: value")
    assert root.get() == 2.0
    root.value = -1.0
    try:
        root.get()
        print("method: unreachable")
    except ValueError:
        print("method: value then caught domain")
    try:
        Roots(-1.0)
        print("constructor: unreachable")
    except ValueError:
        print("constructor: caught domain")

    seen = 0
    try:
        for result in values():
            assert result == 2.0
            seen += 1
            print("generator: first yield")
    except ValueError:
        assert seen == 1
        print("generator: caught after first yield")

    asyncio.run(async_position())

    # Comprehension: guard skips the invalid scalar and container results agree.
    roots = [sqrt(x) for x in [-1.0, 4.0, 9.0] if x >= 0.0]  # tpyc: ok
    assert roots[0] == 2.0 and roots[1] == 3.0 and len(roots) == 2
    print("comprehension: guarded values")
    try:
        bad_roots = [sqrt(x) for x in [4.0, -1.0]]  # tpyc: ok
        print("comprehension: unreachable", len(bad_roots))
    except ValueError:
        print("comprehension: caught included domain")

    closure_position(16.0)
    closure_position(-1.0)

    finalized = 0
    try:
        # Context-manager body: exit follows finally and precedes the outer catch.
        with Unwind():
            try:
                # try/finally: checked failure must unwind exactly once.
                sqrt(-1.0)  # tpyc: ok
                print("try/finally: unreachable")
            finally:
                finalized += 1
                print("try/finally: finally")
    except ValueError:
        assert finalized == 1
        print("context manager: outer catch")

    try:
        assert error_return_root(25.0) == 5.0
        print("@error_return: value")
    except MarkerError:
        print("@error_return: unexpected marker")
    try:
        try:
            error_return_root(-1.0)
            print("@error_return: unreachable")
        except MarkerError:
            print("@error_return: unexpected marker")
    except ValueError:
        print("@error_return: caught ordinary domain")

    # Shapes: tuple elements and narrowed optional/union reads stay scalar calls.
    one = (sqrt(4.0),)  # tpyc: ok
    two = (sqrt(9.0), 1)  # tpyc: ok
    assert sqrt(one[0] * 2.0) == 2.0  # tpyc: ok
    assert one[0] == 2.0 and two[0] == 3.0 and two[1] == 1
    assert optional_root(4.0) == 2.0 and optional_root(None) == 0.0  # tpyc: ok
    # Prebuilt unions avoid BUGS.md#assert-value-union-argument-temp.
    positive_union: float | str = 9.0  # tpyc: ok
    skipped_union: float | str = "skip"  # tpyc: ok
    assert union_root(positive_union) == 3.0  # tpyc: ok
    assert union_root(skipped_union) == 0.0  # tpyc: ok
    for x in [4.0, -1.0]:
        try:
            assert optional_root(x) == 2.0  # tpyc: ok
        except ValueError:
            print("shapes: optional caught domain")
        scalar_union: float | str = x  # tpyc: ok
        try:
            assert union_root(scalar_union) == 2.0  # tpyc: ok
        except ValueError:
            print("shapes: union caught domain")
        try:
            assert generic_root(0, x) == 2.0  # tpyc: ok
        except ValueError:
            print("shapes: generic caught domain")
        try:
            assert mono_root(0, x) == 2.0  # tpyc: ok
        except ValueError:
            print("shapes: monomorphic caught domain")
    print("shapes: tuple, optional, union and generic scalar twins")


# Module-level statement: both global initialization and caught failure run here.
global_root = sqrt(36.0)  # tpyc: ok
assert global_root == 6.0
try:
    sqrt(-1.0)  # tpyc: ok
    print("module level: unreachable")
except ValueError:
    print("module level: value then caught domain")


def main() -> None:
    unary_policy()
    binary_policy()
    ulp_policy()
    positions()
    evaluation()


main()
