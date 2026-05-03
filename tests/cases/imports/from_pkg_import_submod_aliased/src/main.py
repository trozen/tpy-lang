# `from pkg import submod as alias` -- the alias must work as a namespace
# qualifier for both calls and type annotations, just like the un-aliased
# form. Pre-fix the alias rebuild for `_reverse_module_aliases` only fired
# for the un-aliased case; this pins the aliased path.
from pkg import state as p
from tpy import Int32


def use(c: p.Counter) -> Int32:
    return c.n


def main() -> None:
    print(p.LIMIT)
    print(use(p.Counter(Int32(7))))


main()
