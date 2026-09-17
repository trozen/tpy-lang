# Inverse of the promotion/reject cases: views whose source OUTLIVES the
# binding stay zero-copy views and keep compiling (the fix must not
# over-trigger). A view of a parameter, an explicit StrView of a parameter, and
# a slice of a parameter are all view-safe; the values are read to prove the
# views are live. The last two RETURN an unannotated view local at a
# `-> StrView` / `-> BytesView` signature: the storage the deduction settles on
# is what the return check follows, so a local that stays a view is handed back
# rather than rejected (the demoted twin is error_return_demoted_view_local).
# Two further sections repeat that return at a NESTED def position, plain and
# inside a generator: a nested body resolves its own view locals, so the
# returned view is not a borrow of owned storage the callee just destroyed.
from tpy import BytesView, StrView, int32
from typing import Iterator


def view_of_param(s: str) -> None:
    v = s.strip()           # tpyc: type(StrView)
    print(v)


def explicit_view_of_param(s: str) -> None:
    v: StrView = s          # pinned view of a stable source -> allowed
    print(v)


def slice_of_param(s: str) -> None:
    v = s[0:3]              # tpyc: type(StrView)
    print(v)


def return_inferred_strview(s: str) -> StrView:
    v = s.strip()           # tpyc: ok
    return v


def return_inferred_bytesview(ba: bytearray) -> BytesView:
    v = ba[1:]              # tpyc: ok
    return v


# nested def: same return, one level in
def nested_def_return(s: str) -> None:
    def inner_str(t: str) -> StrView:
        v = t.strip()       # tpyc: ok
        return v

    def inner_bytes(b: bytes) -> BytesView:
        v = b[0:16]         # tpyc: ok
        return v

    print("nested_def:", inner_str(s),
          bytes(inner_bytes(b"0123456789abcdefghijklmnop")))


# generator: the nested def inside it is still a body of its own
def gen_nested_def_return(s: str) -> Iterator[int32]:
    def inner(t: str) -> StrView:
        v = t.strip()       # tpyc: ok
        return v

    yield 0
    print("gen_nested_def:", inner(s))


def main() -> None:
    view_of_param("  trimmed  ")
    explicit_view_of_param("kept")
    slice_of_param("abcdef")
    print(return_inferred_strview("  a padded value long enough to show  "))
    buf = bytearray(b"0123456789abcdefghijklmnop")
    print(bytes(return_inferred_bytesview(buf)))
    nested_def_return("  a padded value long enough to show  ")
    for step in gen_nested_def_return("  another padded value, long too  "):
        print("gen_nested_def step:", step)


main()
