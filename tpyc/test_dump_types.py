"""Tests for builtin type dump output."""

from .dump_types import dump_builtin_types, _format_signature
from .modules import MethodDef
from .typesys import INT32


def test_dump_types_includes_readonly_annotations(capsys):
    dump_builtin_types()
    out = capsys.readouterr().out

    assert "`@readonly __getitem__(index: Int32) -> T`" in out
    assert "`append(value: Own[T]) -> None`" in out


def test_format_signature_renders_all_supported_annotations():
    overload = MethodDef(params=[], returns=INT32, cpp="0", is_noalloc=True, is_readonly=True)
    assert _format_signature("f", overload) == "@noalloc @readonly f() -> Int32"
