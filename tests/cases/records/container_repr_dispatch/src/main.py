# Container element printing uses __repr__ (Python: print([rec]) goes
# through obj.__repr__()), while direct print(rec) keeps using __str__.
# Records without __repr__ get the default `<Class object at 0xADDR>` form
# inside containers, even when __str__ is defined.
from tpy import int32

class Both:
    def __init__(self, n: int32) -> None:
        self.n = n
    def __str__(self) -> str:
        return f"Both_str({self.n})"
    def __repr__(self) -> str:
        return f"Both_repr({self.n})"

class StrOnly:
    def __init__(self, n: int32) -> None:
        self.n = n
    def __str__(self) -> str:
        return f"StrOnly_str({self.n})"

class ReprOnly:
    def __init__(self, n: int32) -> None:
        self.n = n
    def __repr__(self) -> str:
        return f"ReprOnly_repr({self.n})"

class Neither:
    def __init__(self, n: int32) -> None:
        self.n = n

class ChildOfRepr(ReprOnly):
    pass


def matches_default_repr(s: str, cls: str) -> bool:
    # CPython adds the module prefix (e.g. `<__main__.StrOnly object at 0x...>`)
    # while TPy emits the bare class name. Accept either to keep the test
    # runnable under both interpreters.
    p1 = "<" + cls + " object at 0x"
    p2 = "<__main__." + cls + " object at 0x"
    return (s.startswith(p1) or s.startswith(p2)) and s.endswith(">")


def main() -> None:
    b = Both(1)
    s = StrOnly(2)
    r = ReprOnly(3)

    # Direct print: __str__ first, fallback to __repr__.
    print(b)
    print(s)
    print(r)

    # repr() builtin: __repr__ first, then default. Never __str__.
    print(repr(b))
    print(matches_default_repr(repr(s), "StrOnly"))
    print(repr(r))

    # Container print: each element through __repr__ then default.
    print([b])
    bs: list[StrOnly] = [s]
    bs_str = str(bs)
    print((bs_str.startswith("[<StrOnly object at 0x")
           or bs_str.startswith("[<__main__.StrOnly object at 0x"))
          and bs_str.endswith(">]"))
    print([r])

    # Tuple and dict containers dispatch through __repr__.
    print((b, r))
    d: dict[str, ReprOnly] = {"a": r}
    print(d)

    # Optional[Record] (lowers to a nullable pointer): None prints as
    # "None"; non-null dispatches through the underlying record's repr.
    opt_some: ReprOnly | None = ReprOnly(7)
    opt_none: ReprOnly | None = None
    print(repr(opt_some))
    print(repr(opt_none))

    # Record with neither __str__ nor __repr__: repr() returns the
    # default form directly (verifies the __tpy_class_name__ template
    # match, not just the container path).
    n = Neither(8)
    print(matches_default_repr(repr(n), "Neither"))

    # Inherited __repr__ from a non-native ancestor: ChildOfRepr should
    # NOT get a default-repr template binding (parent has __repr__).
    c = ChildOfRepr(9)
    print(repr(c))

    # f-string `!r` exercises a different codegen path than repr() but
    # routes through the same tpy::repr_of dispatch. Restricted to records
    # with __repr__ since sema's Representable check rejects !r on records
    # without __repr__ (separate known limitation in TODO.md).
    print(f"{b!r}")
    print(f"{r!r}")

    # set[Record] would also dispatch per element through repr_of, but
    # TPy requires user records to define __hash__ before they can be
    # set elements; covering it here would distract from the dispatch
    # behaviour under test.


main()
