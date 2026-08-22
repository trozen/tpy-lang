"""The qualcall loop's protocol-slot hoist for a RECORD rvalue, plus the
wrapper-union discard and the storage-call slot engage.

A module callee's structural param binds `T&`, so a temporary argument must
be materialized first (`auto __tmp_N = <rvalue>;`). The temp is un-spelled:
`temps.create` renders a protocol slot `auto`, not the record's own type.
"""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical, _compile, _entry, _lower_ctx_witnessed,
)


def _fallbacks(source: str) -> dict:
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False,
                               thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestQualcallProtocolRecordTemp:
    def test_record_rvalue_at_a_structural_slot_hoists_auto(self):
        src = (
            "import json\n"
            "import io\n"
            "def main() -> None:\n"
            "    v = json.load(io.StringIO('{\"a\": 1}'))\n"
            "    print(json.dumps(v, sort_keys=True))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("argtemp.marker_protocol_record", 0) == 1
        out = hpp + cpp
        assert 'auto __tmp_1 = ::tpystd::io::StringIO("{\\"a\\": 1}");' in out
        assert "::tpystd::json::load(__tmp_1);" in out

    def test_lvalue_receiver_needs_no_temp(self):
        # BOUNDARY: a NAME source is already storage, so the structural slot
        # binds it directly -- the hoist is keyed on the rvalue, not on the
        # slot being structural.
        src = (
            "import json\n"
            "import io\n"
            "def main() -> None:\n"
            "    src = io.StringIO('{\"b\": 2}')\n"
            "    v = json.load(src)\n"
            "    print(json.dumps(v, sort_keys=True))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("argtemp.marker_protocol_record", 0) == 0
        assert "::tpystd::json::load(src);" in hpp + cpp

    def test_discarded_wrapper_union_result_routes(self):
        # A qualcall whose wrapper-union result is dropped at the semicolon
        # renders the bare call statement -- the record/container discard
        # rows' union sibling.
        src = (
            "import json\n"
            "import io\n"
            "def main() -> None:\n"
            "    try:\n"
            "        json.load(io.StringIO('{bad'))\n"
            "    except json.JSONDecodeError:\n"
            "        print('err')\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("method.qualcall.union_discard", 0) == 1
        assert "::tpystd::json::load(__tmp_1);" in hpp + cpp

    def test_wrapper_union_storage_call_engages_the_hoist_slot(self):
        # A with-block hoist declares `std::optional<JsonValue> v;`, and the
        # in-block bind writes the by-value result PLAINLY -- no lift.
        src = (
            "import json\n"
            "import io\n"
            "def main() -> None:\n"
            "    with io.StringIO('{\"c\": 3}') as f:\n"
            "        v = json.load(f)\n"
            "    print(json.dumps(v, sort_keys=True))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::optional<::tpystd::json::JsonValue> v;" in out
        assert "v = ::tpystd::json::load(f);" in out
