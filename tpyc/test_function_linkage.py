"""Pin FunctionLinkage to a single enum class.

A second `FunctionLinkage` definition makes a `FunctionInfo.linkage`
member from one class never match `linkage in (OtherClass.NATIVE, ...)`,
so the linkage predicates (is_native_import / is_stub / is_native /
is_native_c / is_extern_c) silently read False for methods. The
predicates must hold for a method's FunctionInfo built through the real
parse->registration path, not just for a synthetic construct.
"""
from __future__ import annotations

from tpyc.typesys import FunctionLinkage as TypesysLinkage
from tpyc.parse.nodes import FunctionLinkage as NodesLinkage
from tpyc.compiler import Compiler


def test_function_linkage_is_a_single_class():
    assert TypesysLinkage is NodesLinkage


def test_native_method_registers_as_native():
    # Exercises registration's method-FunctionInfo construction (the site
    # that assigns method.linkage): a registered @native method must report
    # the linkage predicates correctly, not just a hand-built FunctionInfo.
    src = (
        "from tpy.extern import native\n"
        "from tpy import Int32\n"
        "@native\n"
        "class Cell:\n"
        "    n: Int32\n"
        "@native\n"
        "class Box:\n"
        "    @native('get')\n"
        "    def get(self) -> Cell: ...\n"
    )
    mods = Compiler.from_source(src).compile()
    fi = mods[0].analyzer.registry.get_record("Box").get_method("get")
    assert fi.is_method
    assert fi.is_native
    assert fi.is_native_import
