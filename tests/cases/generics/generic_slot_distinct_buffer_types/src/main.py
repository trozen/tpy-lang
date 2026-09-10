# The INVERSE of generic_slot_view_form_positions: ONE generic instantiated at
# `str`, `String`, `bytes`, `bytearray` and `list[UInt8]`, each of which renders
# its own C++ type -- so `param_val_or_ref_t<T>` at the slot resolves to the
# monomorphic twin's own parameter form per instantiation (a view at str/bytes,
# a reference at String/bytearray/list[UInt8]) and no call site materializes a
# copy. Each section prints the generic's answer beside its twin's on one line.
from tpy import Int32, String, UInt8, readonly


def has_item[T](xs: list[T], v: T) -> bool:
    for x in xs:
        # the open-`T` compare: the element is `T` storage and `v` the slot's
        # parameter form, which at bytes are two different C++ types
        if x == v:
            return True
    return False


def has_item_str(xs: list[str], v: str) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def has_item_string(xs: list[String], v: String) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def has_item_bytes(xs: list[bytes], v: bytes) -> bool:
    for x in xs:
        if x == v:
            return True
    return False


def size_of(p: bytes) -> Int32:
    # a BORROWING `bytes` slot: the parameter form is the span, so a bytearray
    # argument is viewed, never copied -- the render carries no copy helper
    return len(p)


def echo_ref[T](v: T) -> T:
    # a by-value `T` return of a `T` param: `val_or_ref_t<T>` is `T` at a value
    # instantiation (so the view is constructed into it) and `T&` at a
    # reference one (so the borrow passes through)
    return v


def store[T](xs: list[T], v: T) -> None:
    # the body position a view slot cannot serve: a store into `T` storage
    xs.append(v)


def store_str(xs: list[str], v: str) -> None:
    xs.append(v)


def store_bytes(xs: list[bytes], v: bytes) -> None:
    xs.append(v)


class Peeker[T]:
    def peek(self, xs: list[T], v: readonly[T]) -> bool:
        # a `readonly[T]` METHOD slot renders `readonly_form_t<T>` -- the same
        # parameter form, const-qualified only where it is a mutable reference,
        # so a view binds here as bare as at the plain `T` slot
        for x in xs:
            if x == v:
                return True
        return False


def str_family(k: str) -> None:
    # str: the slot is the view, so the caller passes the param bare
    names = ["a", "b"]
    print("str", has_item(names, k), has_item_str(names, k))  # tpyc: ok
    # String: a value type whose slot is a reference to its own storage
    s = String("a")
    strings: list[String] = [String("a"), String("b")]
    print("String", has_item(strings, s), has_item_string(strings, s))  # tpyc: ok


def bytes_family(p: bytes) -> None:
    # bytes: the slot is the span, so the caller passes the param bare
    keys = [b"a", b"b"]
    print("bytes", has_item(keys, p), has_item_bytes(keys, p))  # tpyc: ok
    # bytearray: a REFERENCE type, so both the slot and the `T` return are
    # references -- mutating through the returned alias and observing it on the
    # original is what proves the generic did not copy. (`list[bytearray]` is a
    # separate pre-existing gap, so the container form of this leg is the
    # `list[UInt8]` section below.)
    ba = bytearray(b"a")
    r = echo_ref(ba)  # tpyc: ok
    r.append(9)
    print("bytearray", len(ba), len(r))
    # The BORROWING direction of the same pair, the counterpart of the owning
    # sink in tests/cases/bytes/error_bytearray_at_bytes_sink: a bytearray at a
    # `bytes` parameter is a free view, so the second call sees the mutation
    # made between the two -- and it reads the same in CPython, which passes
    # the object itself. What rules out a per-call copy is the snapshot beside
    # it: the argument renders bare, with no `bytes_copy`.
    buf = bytearray(b"ab")
    n1 = size_of(buf)  # tpyc: ok
    buf.append(7)
    print("borrow", n1, size_of(buf))


def u8_list() -> None:
    # list[UInt8] keeps the plain buffer spelling and its reference forms
    v: list[UInt8] = [UInt8(1)]
    # the container literal COPIES its element into the owned slot; the boundary
    # under test is the generic SLOT, whose reference form the alias below proves
    xs: list[list[UInt8]] = [v]
    print("list", has_item(xs, v))  # tpyc: ok
    r = echo_ref(v)
    r.append(UInt8(2))
    print("list", len(v), len(r))


def storing(k: str, p: bytes) -> None:
    # the store body at both view families, each beside its twin
    a: list[str] = []
    b: list[str] = []
    store(a, k)
    store_str(b, k)
    print("store", a, b)
    c: list[bytes] = []
    d: list[bytes] = []
    store(c, p)
    store_bytes(d, p)
    print("store", c, d)


def readonly_method(k: str) -> None:
    names = ["a", "b"]
    pk = Peeker[str]()
    print("readonly", pk.peek(names, k))  # tpyc: ok


def main() -> None:
    str_family("a")
    bytes_family(b"a")
    u8_list()
    storing("z", b"z")
    readonly_method("a")


main()
