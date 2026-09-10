# A view-form `str`/`bytes` source at a generic `T` parameter slot, in every
# position a call can sit in. Each str/bytes type renders its own C++ type, so
# `param_val_or_ref_t<T>` at the slot IS the monomorphic twin's own parameter
# form and the view binds bare -- NO position here buys an owned temp, including
# the three that spell their slot const (a `readonly[T]` param, a readonly
# method's `T`, a constructor's), which render `readonly_form_t<T>`: the same
# form, const-qualified only where it is a mutable reference. The
# `generic_slot_distinct_buffer_types` case is the inverse, one generic at all
# five buffer types. Each section prints its own name and its monomorphic twin's
# answer on the same line, so a position where the generic and the twin disagree
# names itself.
from typing import Iterator
import asyncio
from tpy import Equatable, Own, ReturnException, error_return, readonly


def has_item[T: Equatable](xs: list[T], v: T) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def has_item_str(xs: list[str], v: str) -> bool:
    # The monomorphic twin: `v` is spelled std::string_view, no copy.
    for x in xs:
        if x == v:
            return True
    return False


def peek[T](xs: list[T], v: readonly[T]) -> bool:
    # A `readonly[T]` slot renders `const T&` -- the same owning binding the
    # bare `T` slot resolves to, so a view owes the same copy.
    for x in xs:
        if x == v:
            return True
    return False


def peek_str(xs: list[str], v: readonly[str]) -> bool:
    # The monomorphic twin of peek[str]: `readonly[str]` is still the view.
    for x in xs:
        if x == v:
            return True
    return False


def peek_bytes(xs: list[bytes], v: readonly[bytes]) -> bool:
    # The monomorphic twin of peek[bytes]: `readonly[bytes]` is still the span.
    for x in xs:
        if x == v:
            return True
    return False


class Boxed[T]:
    v: T

    def __init__(self, value: T) -> None:
        self.v = value  # tpyc: warning(/may copy T into field/)


class OwnBoxed[T]:
    # INVERSE: an `Own[T]` slot spells the owned type by value already, so
    # `has_view_param_form` never reaches it.
    v: T

    def __init__(self, value: Own[T]) -> None:
        # An Own[T] source MOVES into the field, so no copy warning here.
        self.v = value  # tpyc: ok

    def put(self, value: Own[T]) -> None:
        self.v = value  # tpyc: ok


class MyErr(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


NAMES = ["a", "b"]
KEY = "a"
# module-level statement
print("module_level", has_item(NAMES, KEY), has_item_str(NAMES, KEY))  # tpyc: ok


def free_function(k: str) -> None:
    # free function: a `str` PARAM is a view
    print("free_function", has_item(NAMES, k), has_item_str(NAMES, k))  # tpyc: ok


class Holder:
    labels: list[str]

    def __init__(self) -> None:
        self.labels = ["a", "b"]

    def method(self, k: str) -> None:
        # method
        xs = self.labels  # the field read is bound first: BUGS.md#generic-composite-slot-field-arg
        print("method", has_item(xs, k), has_item_str(xs, k))  # tpyc: ok


def ctor_arg(k: str) -> None:
    # ctor arg: the generic record's `explicit Boxed(const T&)` slot
    b = Boxed[str](k)  # tpyc: ok
    print("ctor_arg", b.v == k, k == k)


def comprehension(ks: list[str]) -> None:
    # comprehension
    out = [has_item(NAMES, k) for k in ks]  # tpyc: ok
    twin = [has_item_str(NAMES, k) for k in ks]
    print("comprehension", out, twin)


def gen_body(k: str) -> Iterator[bool]:
    # generator body
    yield has_item(NAMES, k)  # tpyc: ok -- direct, no two-step needed
    yield has_item_str(NAMES, k)


async def async_body(k: str) -> bool:
    # async body (the resumable frame captures the str param OWNED)
    await asyncio.sleep(0.0)
    # direct, no two-step needed: the instantiated body takes `str`'s own
    # forms, so nothing has to be materialized into a temp here
    return has_item(NAMES, k) and has_item_str(NAMES, k)  # tpyc: ok


def closure(k: str) -> None:
    def inner() -> bool:
        # closure: `k` is captured from the enclosing signature, still a view
        return has_item(NAMES, k)  # tpyc: ok

    print("closure", inner(), has_item_str(NAMES, k))


def match_arm(k: str) -> None:
    n = len(k)
    match n:
        case 1:
            # match arm
            print("match_arm", has_item(NAMES, k), has_item_str(NAMES, k))  # tpyc: ok
        case _:
            print("match_arm", False, False)


def cond_operand(k: str, flag: bool) -> None:
    # conditional operand: the temp banks into the short-circuit region
    print("cond_operand", flag or has_item(NAMES, k),  # tpyc: ok
          flag or has_item_str(NAMES, k))


def with_body(k: str) -> None:
    with Ctx():
        # context-manager body
        print("with_body", has_item(NAMES, k), has_item_str(NAMES, k))  # tpyc: ok


def try_finally(k: str) -> None:
    try:
        # try body
        print("try_finally", has_item(NAMES, k), has_item_str(NAMES, k))  # tpyc: ok
    finally:
        print("try_finally", "done")


@error_return(MyErr)
def er_body(k: str) -> bool:
    # @error_return body
    return has_item(NAMES, k)  # tpyc: ok


@error_return(MyErr)
def er_body_str(k: str) -> bool:
    # ... and its monomorphic twin, in the same body shape.
    return has_item_str(NAMES, k)


def bytes_positions(k: bytes) -> None:
    # the bytes family at the same slot, whose resolved slot is a MUTABLE ref
    keys = [b"a", b"b"]
    print("bytes_value", has_item(keys, k))  # tpyc: ok


def readonly_slot(k: str, b: bytes) -> None:
    # readonly[T] free function, str and bytes, each beside its twin: the const
    # slot is `readonly_form_t<T>`, which at a view family IS the view
    keys = [b"a", b"b"]
    print("readonly_slot", peek(NAMES, k), peek_str(NAMES, k))  # tpyc: ok
    print("readonly_slot", peek(keys, b), peek_bytes(keys, b))  # tpyc: ok


class Labels[T]:
    items: list[T]

    def __init__(self, first: T) -> None:
        self.items = [first]  # tpyc: warning(/may copy T into owned storage/)

    def has(self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


def while_cond(k: str) -> None:
    # A compound `while` condition has no statement to hoist a temp into, which
    # is why all three seams used to reject here. None of them needs a temp now:
    # the free call's slot, the (readonly-inferred) METHOD's and the
    # CONSTRUCTOR's all resolve to the caller's own read form.
    n = 0
    while has_item(NAMES, k) and n < 1:  # tpyc: ok
        n += 1
    box = Labels[str]("a")
    while box.has(k) and n < 2:  # tpyc: ok
        n += 1
    while Labels[str](k).has("a") and n < 3:  # tpyc: ok
        n += 1
    print("while_cond", n, has_item_str(NAMES, k))


def inverse(k: str, n: int) -> None:
    # INVERSE: a scalar instantiation must not gain the materialize, and a
    # concrete `str` param slot keeps its view.
    nums = [1, 2]
    print("inverse", has_item(nums, n), has_item_str(NAMES, k))  # tpyc: ok
    # ... a str LITERAL at a generic ctor slot keeps its pre-existing render,
    # and an `Own[T]` slot is not this arm's shape at all.
    lit = Boxed[str]("hi")  # tpyc: ok
    own = OwnBoxed[str]("seed")
    own.put(k)
    print("inverse", lit.v, own.v)


def main() -> None:
    free_function("a")
    while_cond("a")
    Holder().method("a")
    ctor_arg("a")
    comprehension(["a", "z"])
    for g in gen_body("a"):
        print("gen_body", g)
    print("async_body", asyncio.run(async_body("a")))
    closure("a")
    match_arm("a")
    cond_operand("a", False)
    with_body("a")
    try_finally("a")
    try:
        print("er_body", er_body("a"), er_body_str("a"))
    except MyErr:
        print("er_body", "raised")
    bytes_positions(b"a")
    readonly_slot("a", b"a")
    inverse("a", 1)


main()
