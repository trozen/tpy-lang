# tpy: ext_module
# Container marshalling across the @export boundary: list/dict/set/tuple cross
# O(n) by-copy (docs/CPYTHON_INTEROP.md "container cliff"), recursively for
# nested containers and str/bytes elements. The boundary deliberately COPIES --
# a param is an owned snapshot, a return is a fresh object -- so these are pure
# value-in/value-out transforms; the copy semantics (a param mutation NOT
# written back to the caller) are asserted in ext_checks.py, where they diverge
# from the aliasing CPython source.
from tpy import Own
from tpy.extern import export


@export
def sum_list(xs: list[int]) -> int:
    s = 0
    for x in xs:
        s += x
    return s


@export
def doubled(xs: list[int]) -> Own[list[int]]:
    out: list[int] = []
    for x in xs:
        out.append(x * 2)
    return out


@export
def total_len(words: list[str]) -> int:
    n = 0
    for w in words:
        n += len(w)
    return n


@export
def shout(words: list[str]) -> Own[list[str]]:
    out: list[str] = []
    for w in words:
        out.append(w + "!")
    return out


@export
def dict_sum(d: dict[str, int]) -> int:
    s = 0
    for v in d.values():
        s += v
    return s


@export
def histogram(words: list[str]) -> Own[dict[str, int]]:
    h: dict[str, int] = {}
    for w in words:
        h[w] = h.get(w, 0) + 1
    return h


@export
def set_size(s: set[int]) -> int:
    return len(s)


@export
def to_set(xs: list[int]) -> Own[set[int]]:
    out: set[int] = set()
    for x in xs:
        out.add(x)
    return out


@export
def make_pair(n: int) -> tuple[int, str]:
    return (n, "n=" + str(n))


@export
def swap(p: tuple[int, str]) -> tuple[str, int]:
    return (p[1], p[0])


@export
def flatten(m: dict[str, list[int]]) -> Own[list[int]]:
    out: list[int] = []
    for v in m.values():
        for x in v:
            out.append(x)
    return out


@export
def byte_lengths(chunks: list[bytes]) -> Own[list[int]]:
    out: list[int] = []
    for c in chunks:
        out.append(len(c))
    return out


@export
def append_to(xs: list[int], v: int) -> int:
    # Mutates a copy-in param: the boundary warns, and the mutation is not
    # visible to the caller (proven in ext_checks.py). Aliases in the source.
    xs.append(v)
    return len(xs)
