"""A whole value-repr Optional FIELD read at an exactly-matching call slot.

`std::optional<T>` is a value passed by value, so the bare member read binds
the slot with no lift, shim or temp -- the field twin of the whole value-opt
NAME pass-through rows. Corpus witness: http.client's two `connect` bodies
(`socket.create_connection(addr, self.timeout)` at `float | None`)."""

from .testutil import (
    _reject_tally, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_MOD = ("from tpy import Int32\n"
        "def dial(host: str, timeout: float | None) -> Int32:\n"
        "    return 0 if timeout is None else 1\n")


def _reject_tags(src: str, extra_lib_dirs=None):
    return _reject_tally(src, extra_lib_dirs=extra_lib_dirs)


class TestWholeValueOptFieldArg:
    MAIN = ("import thir_valopt_field_mod as m\n"
            "from tpy import Int32\n"
            "class Conn:\n"
            "    host: str\n"
            "    timeout: float | None\n"
            "    def __init__(self, h: str, t: float | None) -> None:\n"
            "        self.host = h\n"
            "        self.timeout = t\n"
            "    def go(self) -> Int32:\n"
            "        return m.dial(self.host, self.timeout)\n"
            "def main() -> None:\n"
            "    print(Conn('h', None).go())\n"
            "main()\n")

    NARROWED = ("import thir_valopt_field_mod as m\n"
                "from tpy import Int32\n"
                "class Conn:\n"
                "    host: str\n"
                "    timeout: float | None\n"
                "    def __init__(self, h: str, t: float | None) -> None:\n"
                "        self.host = h\n"
                "        self.timeout = t\n"
                "    def go(self) -> Int32:\n"
                "        if self.timeout is None:\n"
                "            return 0\n"
                "        return m.dial(self.host, self.timeout)\n"
                "def main() -> None:\n"
                "    print(Conn('h', 1.0).go())\n"
                "main()\n")

    def _dirs(self, tmp_path):
        (tmp_path / "thir_valopt_field_mod.py").write_text(_MOD)
        return [tmp_path]

    def test_routes_witnessed(self, tmp_path):
        dirs = self._dirs(tmp_path)
        _, witnesses = _lower_ctx_witnessed(self.MAIN, extra_lib_dirs=dirs)
        assert witnesses.get("arg.value_opt_field", 0) >= 1
        assert witnesses.get("field.whole_optional", 0) >= 1
        _assert_routes_byte_identical(self.MAIN, extra_lib_dirs=dirs)

    def test_narrowed_field_derefs_and_routes(self, tmp_path):
        # The narrowed read is a DIFFERENT render (`(*this->timeout)`); the
        # exact-type pin keeps it off the whole-optional arm, and it must
        # keep routing through the arm it already had.
        dirs = self._dirs(tmp_path)
        _, witnesses = _lower_ctx_witnessed(self.NARROWED, extra_lib_dirs=dirs)
        assert witnesses.get("arg.value_opt_field", 0) == 0
        _assert_routes_byte_identical(self.NARROWED, extra_lib_dirs=dirs)


class TestWholeValueOptFieldArgBoundary:
    STR_MOD = ("from tpy import Int32\n"
               "def dial(name: str | None) -> Int32:\n"
               "    return 0 if name is None else 1\n")

    def test_str_inner_keeps_rejecting(self, tmp_path):
        # A str/bytes inner needs the optional<string_view>/optional<string>
        # shim, not the bare pass.
        (tmp_path / "thir_valopt_field_str.py").write_text(self.STR_MOD)
        src = ("import thir_valopt_field_str as m\n"
               "from tpy import Int32\n"
               "class Conn:\n"
               "    name: str | None\n"
               "    def __init__(self) -> None:\n"
               "        self.name = None\n"
               "    def go(self) -> Int32:\n"
               "        return m.dial(self.name)\n"
               "def main() -> None:\n"
               "    print(Conn().go())\n"
               "main()\n")
        _assert_rejects_at(_reject_tags(src, [tmp_path]),
                           "body:expr.method_call",
                           "method.qualcall.arg.optional")

    def test_pointer_repr_optional_field_keeps_its_lift(self, tmp_path):
        # A pointer-repr `Optional[record]` slot takes optional_to_ptr, a
        # different render -- the value-repr pin must not reach it.
        mod = ("from tpy import Int32\n"
               "class Item:\n"
               "    k: Int32\n"
               "    def __init__(self, k: Int32) -> None:\n"
               "        self.k = k\n"
               "def take(i: Item | None) -> Int32:\n"
               "    return 0 if i is None else i.k\n")
        (tmp_path / "thir_valopt_field_rec.py").write_text(mod)
        src = ("import thir_valopt_field_rec as m\n"
               "from tpy import Int32\n"
               "class Holder:\n"
               "    it: m.Item | None\n"
               "    def __init__(self) -> None:\n"
               "        self.it = None\n"
               "    def go(self) -> Int32:\n"
               "        return m.take(self.it)\n"
               "def main() -> None:\n"
               "    print(Holder().go())\n"
               "main()\n")
        _, witnesses = _lower_ctx_witnessed(src, extra_lib_dirs=[tmp_path])
        assert witnesses.get("arg.value_opt_field", 0) == 0
        assert witnesses.get("optptr.lift", 0) >= 1
        _assert_routes_byte_identical(src, extra_lib_dirs=[tmp_path])
