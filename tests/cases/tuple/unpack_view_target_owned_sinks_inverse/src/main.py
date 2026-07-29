# Inverse of unpack_view_target_owned_sinks: sources at the same owned sinks
# that must NOT gain a view->owned copy -- str literals (const char[N]), an
# already-owned String source, and unpack-target reads at view sinks (compare,
# len, print), which stay zero-copy.
from tpy import String


def literals() -> int:
    xs = ["ab", "c"]
    print(xs[0], xs[1])
    return len(xs)


def owned_source(s: String) -> int:
    xs = [s]
    print(xs[0])
    return len(xs)


def view_reads(src: tuple[str, str]) -> int:
    x, y = src
    n = 0
    if x == "ab":
        n = n + len(x)
    print(y)
    return n


def main() -> None:
    print(literals())
    print(owned_source(String("kept")))
    print(view_reads(("ab", "c")))


main()
