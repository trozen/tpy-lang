# BigInt inline arithmetic retains Python results across boundaries and body positions.
# Explicit int annotations exercise BigInt without changing the default integer type.
import asyncio
from typing import Iterator
from tpy import Comparable, Int32, ReturnException, error_return


def compare(a: int, b: int) -> bool:
    return a < b  # tpyc: ok


def generic_compare[T: Comparable](a: T, b: T) -> bool:
    # The instantiated comparison must use the same operator as its concrete twin.
    return a < b  # tpyc: ok


class Number:
    value: int

    def __init__(self, a: int, b: int) -> None:
        # Constructor: store a floor-rounded result in a BigInt field.
        self.value = a // b  # tpyc: ok

    def check(self, other: int) -> bool:
        # Method: the field's value takes the same signed comparison path.
        return self.value < other  # tpyc: ok


class Counter:
    calls: Int32

    def __init__(self) -> None:
        self.calls = 0

    def get(self, value: int) -> int:
        self.calls += 1
        return value


class Scope:
    def __enter__(self) -> None:
        print("context enter")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("context exit")


class InvalidOperand(Exception, ReturnException):
    pass


@error_return(InvalidOperand)
def checked_floor(a: int, b: int) -> int:
    if b == 0:
        raise InvalidOperand
    # Error-return body: the successful result still uses BigInt floor division.
    return a // b  # tpyc: ok


def generated(a: int, b: int) -> Iterator[int]:
    # Generator: exercise the same payload before and after resumption.
    yield a >> 1  # tpyc: ok
    if a < b:  # tpyc: ok
        yield a % b  # tpyc: ok


async def async_ops(a: int, b: int) -> int:
    before = a // b  # tpyc: ok
    await asyncio.sleep(0)
    # Async body: division and bitwise operations straddle a suspension.
    return before ^ (a >> 1)  # tpyc: ok


def wrappers(value: int | None, variant: int | str, pair: tuple[int, int]) -> None:
    # Optional: operate on the extracted scalar after narrowing.
    if value is not None:
        print("optional", value >> 1, ~value)  # tpyc: ok
    else:
        print("optional none")
    # Union and match arm: only the integer alternative reaches the operator.
    match variant:
        case int() as number:
            print("match int", number & pair[1])  # tpyc: ok
        case str():
            print("match str")
    # Tuple: both arities must extract the same BigInt value form.
    single = (pair[0],)
    print("tuple", single[0] < pair[1], pair[0] // pair[1])  # tpyc: ok


def operators(a: int, b: int) -> None:
    # Free function: all changed binary families, including signed floor identities.
    print("compare", a, b, a == b, a != b, a < b, a <= b, a > b, a >= b)  # tpyc: ok
    print("bitwise", a & b, a | b, a ^ b, ~a)  # tpyc: ok
    if b != 0:
        quotient, remainder = divmod(a, b)  # tpyc: ok
        print("division", a // b, a % b, quotient, remainder)  # tpyc: ok
        print("identity", quotient * b + remainder == a)
        print("remainder", abs(remainder) < abs(b), remainder == 0 or (remainder < 0) == (b < 0))


def exceptions(a: int, zero: int, negative: int) -> None:
    # Try/finally: preserve each runtime guard and stable catch/finally order.
    try:
        print(a // zero)  # tpyc: ok
    except ZeroDivisionError:
        print("errors floor")
    finally:
        print("errors finally")
    try:
        print(a % zero)  # tpyc: ok
    except ZeroDivisionError:
        print("errors modulo")
    try:
        print(divmod(a, zero))  # tpyc: ok
    except ZeroDivisionError:
        print("errors divmod")
    try:
        print(a / zero)  # tpyc: ok
    except ZeroDivisionError:
        print("errors true division")
    try:
        print(a << negative)  # tpyc: ok
    except ValueError:
        print("errors left shift")
    try:
        print(a >> negative)  # tpyc: ok
    except ValueError:
        print("errors right shift")


def main() -> None:
    one: int = 1
    low = -(one << 62)
    high = (one << 62) - 1
    # Free function: all signs, zero/equality, endpoints and neighboring heap values.
    for a, b in [(17, 3), (-17, 3), (17, -3), (-17, -3), (18, -3),
                 (2, 17), (-2, 17), (0, 3), (3, 0), (3, 3)]:
        operators(a, b)
    for a, b in [(low, -one), (high, one), (low, high), (high, low),
                 (high + 1, high), (low - 1, low), (high, high + 1),
                 (low, low - 1)]:
        operators(a, b)

    # Shifts: fixed and BigInt counts, inline endpoints, promotion and wide saturation.
    for shift in [0, 1, 61, 62, 63, 64, 200]:
        print("shift fixed", shift, one << shift, -one << shift, low >> shift, high >> shift)  # tpyc: ok
        count: int = shift
        print("shift bigint", count, one << count, -one << count, low >> count, high >> count)  # tpyc: ok
    print("shift max", low >> 2147483647, high >> 2147483647)  # tpyc: ok
    print("complement endpoints", ~low, ~high)  # tpyc: ok
    print("heap inverse", (one << 200) >> 199, (low - 1) & high)  # tpyc: ok

    # Compound assignment: each form delegates to the same small-result operator.
    compound: int = -17
    compound //= 3  # tpyc: ok
    compound %= 4  # tpyc: ok
    compound <<= 2  # tpyc: ok
    compound >>= 1  # tpyc: ok
    compound |= 3  # tpyc: ok
    compound &= 6  # tpyc: ok
    compound ^= 1  # tpyc: ok
    print("compound", compound)

    # Constructor and method positions share the free function's floor/comparison rules.
    number = Number(-17, 3)
    print("constructor", number.value)
    print("method", number.check(-5))  # tpyc: ok
    print("generic twin", compare(low, high), generic_compare(low, high))  # tpyc: ok
    wrappers(-17, high, (-17, 3))
    wrappers(None, "inverse", (17, 3))

    # Generator and async bodies execute both sides of their resume boundary.
    for value in generated(-17, 3):
        print("generator", value)
    print("async", asyncio.run(async_ops(-17, 3)))

    # Comprehension: comparison filtering and element arithmetic on BigInt list slots.
    values = [low, -one, one, high]
    results = [value >> 1 for value in values if value < 0]  # tpyc: ok
    for value in results:
        print("comprehension", value)

    # Closure: the captured scalar reaches the same signed operator.
    def captured(other: int) -> bool:
        return low < other  # tpyc: ok
    print("closure", captured(-17))

    # Context manager body: floor arithmetic is unchanged within a with region.
    with Scope():
        print("context body", low // 3, low % 3)  # tpyc: ok
    try:
        print("error_return", checked_floor(-17, 3))  # tpyc: ok
        checked_floor(17, 0)
    except InvalidOperand:
        print("error_return caught")
    exceptions(-17, 0, -1)

    # Conditional operands: middle values execute once and later guards stay lazy.
    counter = Counter()
    chain = counter.get(-17) < counter.get(3) < counter.get(17)  # tpyc: ok
    stopped = counter.get(17) < counter.get(3) < counter.get(-17)  # tpyc: ok
    lazy = compare(high, low) and compare(counter.get(1), high)  # tpyc: ok
    print("evaluation", chain, stopped, lazy, counter.calls)

    # True division control: inline operands still require exact rounding beyond 2**53.
    rounded = (one << 60) + (one << 7)
    divisor: int = 3
    zero: int = 0
    print("rounding", rounded / divisor, float(rounded) / float(divisor))  # tpyc: ok
    print("true small", one / divisor, zero / -divisor)  # tpyc: ok


# Module statement: pure operands isolate global slots from argument sequencing.
module_a: int = -17
module_b: int = -3
module_less = module_a < module_b  # tpyc: ok
print("module", module_less)
main()
