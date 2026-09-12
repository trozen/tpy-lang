"""The source-comment writers: block dedent and the numbered form the CLI
emits (`--emit-source` keeps comment_line_numbers on; the snapshot harness
pins it off, so the numbering is only pinned here)."""
import io

from tpyc.codegen_cpp.context import CodeGenContext


class _Options:
    def __init__(self, numbered: bool) -> None:
        self.comment_line_numbers = numbered


class _Ctx:
    def __init__(self, numbered: bool) -> None:
        self.options = _Options(numbered)

    _write_source_lines = CodeGenContext._write_source_lines


def test_block_dedents_by_common_whitespace_only():
    out = io.StringIO()
    _Ctx(False)._write_source_lines(out, 7, [
        "        cfg = {",
        '            "x": 1,',
        "",
        "        }",
    ], "    ")
    assert out.getvalue() == (
        "    // cfg = {\n"
        '    //     "x": 1,\n'
        "    //\n"
        "    // }\n"
    )


def test_numbered_form_counts_every_line_including_blank():
    out = io.StringIO()
    _Ctx(True)._write_source_lines(out, 12, ["    a = 1", "", "    b = 2"], "")
    assert out.getvalue() == "// 12: a = 1\n// 13:\n// 14: b = 2\n"
