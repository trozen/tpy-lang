# A generic body returning `d.get(k, dflt)` at `-> V` is refused: the call is a
# copy there, which `-> Own[V]` returns (docs/LANGUAGE_FEATURES.md dict `get`;
# TODO.md "Per-instantiation result form for a generic body").
from tpy import int32


def g[K, V](d: dict[K, V], k: K, dflt: V) -> V:
    return d.get(k, dflt)  # tpyc: error(/Own\[V\]/)


def main() -> None:
    d = {"a": 1}
    print(g(d, "a", 0))


main()
