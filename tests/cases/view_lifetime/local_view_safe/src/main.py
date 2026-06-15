# Inverse of the promotion/reject cases: views whose source OUTLIVES the
# binding stay zero-copy views and keep compiling (the fix must not
# over-trigger). A view of a parameter, an explicit StrView of a parameter, and
# a slice of a parameter are all view-safe; the values are read to prove the
# views are live.
from tpy import StrView


def view_of_param(s: str) -> None:
    v = s.strip()           # tpyc: type(StrView)
    print(v)


def explicit_view_of_param(s: str) -> None:
    v: StrView = s          # pinned view of a stable source -> allowed
    print(v)


def slice_of_param(s: str) -> None:
    v = s[0:3]              # tpyc: type(StrView)
    print(v)


def main() -> None:
    view_of_param("  trimmed  ")
    explicit_view_of_param("kept")
    slice_of_param("abcdef")


main()
