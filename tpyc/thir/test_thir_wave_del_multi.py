"""Multi-target `del d[a], e[b]` -- THIRDelItem: `_gen_del_item_code`'s
per-target loop, one `::tpy::__delitem__(recv, key);` line per target in
source order. Single-target dels keep the plain THIRExprStmt render; every
per-target admission check (subscript shape, receiver shape, key slice) runs
per target, so one rejecting target folds the whole statement."""

from .testutil import (_assert_routes_byte_identical,
                      _reject_tally)


class TestDelItemMultiTarget:
    SRC = (
        "from tpy import Int32\n"
        "class Bag:\n"
        "    d: dict[str, Int32]\n"
        "    def __init__(self) -> None:\n        self.d = {'k': 1}\n"
        "    def __delitem__(self, key: str) -> None:\n"
        "        del self.d[key]\n"
        "def mixed_kinds() -> None:\n"
        "    d = {'a': Int32(1), 'b': Int32(2)}\n"
        "    xs = [10, 20, 30]\n"
        "    del d['a'], xs[1]\n"
        "    print(d, xs)\n"
        "def record_and_dict() -> None:\n"
        "    b = Bag()\n"
        "    d = {'x': Int32(9), 'y': Int32(8)}\n"
        "    del b['k'], d['y']\n"
        "    print(b.d, d)\n"
        "def three_targets() -> None:\n"
        "    d = {'a': Int32(1), 'b': Int32(2), 'c': Int32(3), 'e': Int32(4)}\n"
        "    del d['a'], d['c'], d['e']\n"
        "    print(d)\n"
        "def main() -> None:\n"
        "    mixed_kinds()\n    record_and_dict()\n    three_targets()\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # One line per target, in source order.
        assert '::tpy::__delitem__(d, "a");\n' in cpp
        assert '::tpy::__delitem__(xs, 1);' in cpp
        assert cpp.index('__delitem__(d, "a")') < cpp.index(
            '__delitem__(xs, 1)')
        # Three-target statement emits all three.
        assert '::tpy::__delitem__(d, "c");' in cpp
        assert '::tpy::__delitem__(d, "e");' in cpp


class TestOneRejectingTargetFoldsStatement:
    # The per-target admission runs inside the loop: ONE rejecting target
    # (a REASSIGNED dict receiver -- a pointer local, the recv_shape
    # reject) folds the whole statement, valid sibling targets included,
    # byte-identically.
    SRC = (
        "from tpy import Int32\n"
        "def f(flag: bool) -> None:\n"
        "    d = {'a': Int32(1), 'b': Int32(2)}\n"
        "    d2 = {'x': Int32(9)}\n"
        "    if flag:\n"
        "        d2 = {'y': Int32(8)}\n"
        "    del d['a'], d2['x']\n"
        "    print(d, d2)\n"
        "def main() -> None:\n    f(False)\n"
        "main()\n"
    )

    def test_mixed_targets_fold_whole_body(self):
        assert any((k.startswith('body:stmt.del_item') for k in _reject_tally(self.SRC))), _reject_tally(self.SRC)
