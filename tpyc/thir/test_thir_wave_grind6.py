"""Wave 6 of the grind loop: the type-ctor str-parse overload.

`Int32(tok)` on a str token resolves the from_str `__init__` overload,
whose own @cpp_template carries the parse render
(`::tpy::from_str_check<int32_t>(tok)`) -- the scalar-ctor arg gate now
admits a str-family arg at a str param slot beside the scalar one.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestTypeCtorStrParse:
    SRC = (
        "from tpy import Int32, StrView\n"
        "def main() -> None:\n"
        "    tok = \"42\"\n"
        "    n = Int32(tok)\n"
        "    print(n)\n"
        "main()\n"
    )

    def test_str_parse_ctor_routes(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "::tpy::from_str_check<int32_t>(tok)" in cpp

    def test_scalar_arg_family_still_routes_plain(self):
        # The adjacent SCALAR family keeps its plain render -- the
        # str-parse widening must not have re-routed a scalar arg through
        # the from_str template. (A record/container arg at a scalar
        # type-ctor resolves no __init__ overload -- a sema error -- so no
        # THIR boundary exists for that family.)
        src = (
            "from tpy import Int32\n"
            "def main() -> None:\n"
            "    xs = [1, 2]\n"
            "    n = Int32(len(xs))\n"
            "    print(n)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "from_str_check" not in cpp
