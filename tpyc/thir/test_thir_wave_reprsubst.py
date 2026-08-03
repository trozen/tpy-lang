"""Generic-static method calls with representational substitution: a
type-param sema marked (`representational_subst_params`) spells its
Adapter override in the method-targ position
(`Rc<Pet>::new_<::tpy::Adapter<Pet, Cat>>(...)`), mirroring
`_representational_param_subst` / `_render_method_type_arg`. Unmarked
calls keep the plain `type_to_cpp` targ render."""

from __future__ import annotations

from .testutil import (
    _fn, _lower_ctx_witnessed, _assert_routes_byte_identical,
)

_HDR = (
    "from typing import Protocol\n"
    "from tpy import dynamic\n"
    "from tplib import Rc\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Cat:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n"
    "        return self.label\n"
)


class TestStaticGenericReprSubst:
    def test_adapter_targ_spelling_routes(self):
        # The LHS hint flips T to Pet; U spells the Adapter override.
        src = (_HDR +
               "def main() -> None:\n"
               "    r: Rc[Pet] = Rc.new(Cat(\"W\"))\n"
               "    print(r.get().name())\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert ("Rc<Pet>::new_<::tpy::Adapter<Pet, Cat>>(Cat(\"W\"))"
                in cpp[1])

    def test_plain_targ_spelling_unaffected(self):
        # No hint -> no repr-subst mark -> plain type_to_cpp targs.
        src = (_HDR +
               "def main() -> None:\n"
               "    r = Rc.new(Cat(\"P\"))\n"
               "    print(r.get().name())\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "Rc<Cat>::new_<Cat>(Cat(\"P\"))" in cpp[1]
