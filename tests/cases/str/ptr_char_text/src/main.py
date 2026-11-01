# A Ptr[char] is a pointer to one char: every text sink renders the char it
# points at, a null one renders None, and a str literal at a position where
# C++ deduces its own type stays a str.
# Read-only after the pointer is taken: CPython's take_ptr stub copies
# (BUGS.md#ptr-value-local-cpython-stub-copies).
from typing import Any, Iterable
from tpy import Ptr, Stringable, char, int8, int32, take_ptr
from tpy.unsafe import unsafe_const_cast, unsafe_load, unsafe_ptr


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Named:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return "Named(" + self.name + ")"


class Holder:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: a Ptr[char] parameter
    def show(self, p: Ptr[char]) -> None:
        print(self.tag, str(p), repr(p), f"{p}", f"{p!r}")  # tpyc: ok
        print(self.tag, p)  # tpyc: ok


# free function: a Ptr[char] parameter
def free_text(tag: str, p: Ptr[char]) -> None:
    print(tag, str(p), repr(p), f"{p}", f"{p!r}")  # tpyc: ok
    print(tag, p)  # tpyc: ok


# free function: a format spec pads the pointee's text. The warning stays
# for a pointer that may be null: TPy pads `None` where CPython's
# None.__format__ raises; `!s` states the padded text and runs on both.
def free_spec(p: Ptr[char]) -> None:
    print("spec", f"[{p:^5}]")  # tpyc: warning(/format spec on Ptr\[char\] pads its str\(\) text/)


# a null pointer padded through `!s`, as CPython pads str(None)
def free_spec_s(p: Ptr[char]) -> None:
    print("spec-s", f"[{p!s:^5}]")  # tpyc: ok


# generic body: T instantiated at Ptr[char] / Ptr[int32]
def gen_text[T](tag: str, x: T) -> None:
    print(tag, str(x), repr(x), f"{x}", f"{x!r}")  # tpyc: ok
    print(tag, x)  # tpyc: ok


# generic body: a Ptr to an open pointee
def gen_ptr[T](tag: str, p: Ptr[T]) -> None:
    print(tag, f"{p}", f"{p!r}")  # tpyc: ok
    print(tag, str(p), repr(p))  # tpyc: ok


# generic body: print of an open T streams the value's own text
def gen_print[T](tag: str, x: T) -> None:
    print(tag, x)  # tpyc: ok


# generic body: a Ptr[char] keys a dict at T
def gen_key[T](p: T) -> None:
    d: dict[T, int32] = {}
    d[p] = 7  # tpyc: ok
    print("gen-key", d[p])


# Stringable parameter: str() and print of the argument
def stext(v: Stringable) -> str:
    return str(v)


def sprint(tag: str, v: Stringable) -> None:
    print(tag, v)  # tpyc: ok


# generic structural protocol parameter given a str literal: print keeps
# the argument's own text
def iprint_chars(items: Iterable[char]) -> None:
    n = 0
    for _ in items:
        n += 1
    print("iter-chars", items, n)  # tpyc: ok


def ternaries(c: bool, d: bool, v: Box | Holder) -> None:
    # literal ternaries: mixed length, nested, isinstance-narrowed, `or`
    print("tern", repr("a" if c else "bc"))  # tpyc: ok
    print("tern", repr("a" if c else ("bcd" if d else "ef")))  # tpyc: ok
    print("tern", repr("aa" if isinstance(v, Box) else "b"))  # tpyc: ok
    print("tern", repr(("ab" if c else "cd") or "ef"))  # tpyc: ok
    # an embedded NUL stays in the view arm
    print("tern", repr("a\0b" if c else "x"))  # tpyc: ok


def main() -> None:
    c: char = "x"
    p = take_ptr(c)
    null: Ptr[char] = None

    free_text("free", p)
    free_text("free-null", null)
    free_spec(p)
    free_spec_s(p)
    free_spec_s(null)

    h = Holder("method")
    h.show(p)
    h.show(null)

    gen_text("gen", p)
    gen_text("gen-null", null)
    gen_ptr("gen-ptr", p)
    n = 5
    pn = take_ptr(n)
    gen_text("gen-int", pn)

    # container element: list[Ptr[char]]
    xs: list[Ptr[char]] = []
    xs.append(p)
    xs.append(null)
    print("list", xs, repr(xs))  # tpyc: ok

    print("stringable", stext(p), stext(null))
    sprint("stringable-print", p)
    sprint("stringable-print-null", null)
    sprint("stringable-int", pn)
    sprint("stringable-bool", True)
    sprint("stringable-list", [1, 2])
    # bytes, bytearray, range and int8 write their own text, not a list
    sprint("stringable-bytes", b"ab")
    sprint("stringable-bytearray", bytearray(b"ab"))
    sprint("stringable-range", range(3))
    small: int8 = 65
    sprint("stringable-int8", small)
    sprint("stringable-str", "hi")
    sprint("stringable-float", 1.5)
    sprint("stringable-char", c)
    sprint("stringable-record", Named("n"))
    gen_print("gen-print-bytes", b"ab")
    gen_print("gen-print-bytearray", bytearray(b"ab"))
    gen_print("gen-print-range", range(3))
    gen_print("gen-print-int8", small)

    iprint_chars("hello")

    # Any holding a Ptr[char]
    a: Any = p
    an: Any = null
    print("any", repr(a), [a], repr(an), [an])  # tpyc: ok
    # Any holding a Ptr[char] hashes as its pointee; a null one as None
    b: Any = p
    bn: Any = null
    print("any-hash", hash(a) == hash(b), hash(an) == hash(bn))  # tpyc: ok
    gen_key(p)
    # Any holding a pointer to a record with no text and no hash stores it
    box = Box(3)
    ab: Any = take_ptr(box)  # tpyc: ok
    print("any-record", "stored")

    # unsafe_ptr points at the first element, and prints it
    s: str = "hello"
    s += "!"
    up = unsafe_ptr(s)
    print("uptr", up, repr(up), f"{up}")  # tpyc: ok
    ys = [4, 5]
    lp = unsafe_ptr(ys)
    print("uptr-list", lp, repr(lp))  # tpyc: ok
    # const_cast keeps pointing at the first element
    cp = unsafe_const_cast(up)
    print("uptr-cast", cp, repr(cp), unsafe_load(cp, 1))  # tpyc: ok

    ternaries(True, False, Box(1))
    ternaries(False, True, Holder("h"))
    ternaries(False, False, Box(2))


main()
