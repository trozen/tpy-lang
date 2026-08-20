# A container literal deferred into a conditional operand's std::optional slot
# needs a typed initializer: a bare brace-init deduces its own element type.
from collections import Counter
from typing import Iterator

from tpy import Array, Float32, Float64, Int8, Int32, Int64


class Tally:
    n: Int32

    def __init__(self) -> None:
        self.n = 0


def bump(t: Tally) -> Int64:
    t.n += 1
    return 1


def take_i64(o: list[Int64]) -> Int64:
    """Mutable reference param, so a literal argument needs a hoisted temp."""
    o.append(4)
    return o[0]


def take_i32(o: list[Int32]) -> Int32:
    o.append(4)
    return o[0]


def take_i8(o: list[Int8]) -> Int8:
    o.append(4)
    return o[0]


def take_f32(o: list[Float32]) -> Float32:
    o.append(4.0)
    return o[0]


def take_f64(o: list[Float64]) -> Float64:
    o.append(4.0)
    return o[0]


def take_arr(o: Array[Int64, 3]) -> Int64:
    o[0] = 9
    return o[0]


def take_nested(o: list[list[Int64]]) -> Int64:
    o.append([9])
    return o[0][0]


def take_dict(o: dict[Int64, Int64]) -> Int64:
    o[9] = 9
    return len(o)


def take_set(o: set[Int64]) -> Int64:
    o.add(9)
    return len(o)


def take_any[T](o: T) -> Int64:
    return 1


def gen(o: list[Int64]) -> Iterator[Int64]:
    for x in o:
        yield x


class Holder:
    n: Int64

    def __init__(self, o: list[Int64]) -> None:
        o.append(4)
        self.n = o[0]


class Boxed[T]:
    n: Int64

    def __init__(self, o: list[Int64]) -> None:
        o.append(4)
        self.n = o[0]


class Bx:
    tag: Int32

    def __init__(self) -> None:
        self.tag = 0

    def gen(self, o: list[Int64]) -> Iterator[Int64]:
        for x in o:
            yield x


# --- the literal's element type differs from the container's ---------------


def free_call(flag: bool) -> Int64:
    return take_i64([1, 2, 3]) if flag else 0  # tpyc: ok


def user_ctor() -> Int64:
    return 0 or Holder([1, 2, 3]).n  # tpyc: ok


def generic_ctor(flag: bool) -> Int64:
    return Boxed[Int64]([1, 2, 3]).n if flag else 0  # tpyc: ok


def free_type_param(flag: bool) -> Int64:
    return 0 if not flag else take_any([1, 2, 3])  # tpyc: ok


def free_generator(flag: bool) -> Int64:
    total = Int64(0)
    for v in (gen([1, 2, 3]) if flag else gen([9])):  # tpyc: ok
        total += v
    return total


def method_generator(bx: Bx, flag: bool) -> Int64:
    total = Int64(0)
    for v in (bx.gen([1, 2, 3]) if flag else bx.gen([9])):  # tpyc: ok
        total += v
    return total


def protocol_union(flag: bool) -> Int64:
    # The everyday-code shape: a stdlib constructor taking an iterable.
    return len(Counter([1, 1, 2])) if flag else 0  # tpyc: ok


def narrow_int(flag: bool) -> bool:
    return flag and take_i8([1, 2, 3]) > 0  # tpyc: ok


def narrow_float(flag: bool) -> Float32:
    return take_f32([1.0, 2.0]) if flag else Float32(0.0)  # tpyc: ok


def fixed_array(flag: bool) -> Int64:
    # std::array has no initializer_list constructor at all, so this shape
    # fails even for an element type that would otherwise survive.
    return take_arr([1, 2, 3]) if flag else 0  # tpyc: ok


def nested_list(flag: bool) -> Int64:
    return take_nested([[1, 2], [3]]) if flag else 0  # tpyc: ok


# --- shapes that compiled either way: only the render pins them ------------


def same_width_int(flag: bool) -> Int32:
    return take_i32([1, 2, 3]) if flag else Int32(0)  # tpyc: ok


def same_width_float(flag: bool) -> Float64:
    return take_f64([1.0, 2.0]) if flag else 0.0  # tpyc: ok


# --- shapes whose render already carries its type --------------------------


def empty_literal(flag: bool) -> Int64:
    return take_i64([]) if flag else 0  # tpyc: ok


def repeat_literal(flag: bool, n: Int32) -> Int64:
    return take_i64([0] * n) if flag else 0  # tpyc: ok


def dict_literal(flag: bool) -> Int64:
    return take_dict({1: 2}) if flag else 0  # tpyc: ok


def set_literal(flag: bool) -> Int64:
    return take_set({1, 2}) if flag else 0  # tpyc: ok


# --- every conditional-operand position ------------------------------------


def and_rhs(flag: bool) -> bool:
    return flag and take_i64([1, 2, 3]) > 0  # tpyc: ok


def or_rhs(flag: bool) -> bool:
    return flag or take_i64([1, 2, 3]) > 0  # tpyc: ok


def ternary_then(flag: bool) -> Int64:
    return take_i64([1, 2, 3]) if flag else 0  # tpyc: ok


def ternary_else(flag: bool) -> Int64:
    return 0 if flag else take_i64([1, 2, 3])  # tpyc: ok


def chained(a: Int64, b: Int64) -> bool:
    # A later comparator is conditional too: skipped once an earlier pair fails.
    return a < b < take_i64([1, 2, 3])  # tpyc: ok


def relocated_decl(flag: bool) -> Int64:
    # The comprehension body flushes the temp into its own scope, so closing
    # the region cannot rewrite the decl into an emplace: the row keeps its
    # `std::optional<T> t = ...;` form, which needs the same typed initializer.
    return sum([take_i64([1, 2, 3]) for _ in range(2)]) if flag else 0  # tpyc: ok


# --- the deferral is a skip, not just a move --------------------------------


def side_effect(t: Tally, flag: bool) -> Int64:
    # A call element: `bump` must run only when the branch is taken, which is
    # what the deferral buys over initializing at the enclosing statement.
    return take_i64([bump(t), 2, 3]) if flag else 0  # tpyc: ok


def main() -> None:
    bx = Bx()
    print("free_call", free_call(False), free_call(True))
    print("user_ctor", user_ctor())
    print("generic_ctor", generic_ctor(False), generic_ctor(True))
    print("free_type_param", free_type_param(False), free_type_param(True))
    print("free_generator", free_generator(False), free_generator(True))
    print("method_generator", method_generator(bx, False),
          method_generator(bx, True))
    print("protocol_union", protocol_union(False), protocol_union(True))
    print("narrow_int", narrow_int(False), narrow_int(True))
    print("narrow_float", narrow_float(False), narrow_float(True))
    print("fixed_array", fixed_array(False), fixed_array(True))
    print("nested_list", nested_list(False), nested_list(True))
    print("same_width_int", same_width_int(False), same_width_int(True))
    print("same_width_float", same_width_float(False), same_width_float(True))
    print("empty_literal", empty_literal(True))
    print("repeat_literal", repeat_literal(True, 2))
    print("dict_literal", dict_literal(True))
    print("set_literal", set_literal(True))
    print("and_rhs", and_rhs(False), and_rhs(True))
    print("or_rhs", or_rhs(True), or_rhs(False))
    print("ternary_then", ternary_then(False), ternary_then(True))
    print("ternary_else", ternary_else(True), ternary_else(False))
    print("chained", chained(5, 1), chained(-5, -1))
    print("relocated_decl", relocated_decl(False), relocated_decl(True))

    t = Tally()
    print("side_effect_skipped", side_effect(t, False), t.n)
    print("side_effect_taken", side_effect(t, True), t.n)


main()
