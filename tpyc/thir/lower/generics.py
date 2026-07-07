"""Type-arg spelling helpers for the generics frontier.

The invariant: every type THIR spells at a use site is rendered by the
same function the AST emit arm uses -- raw `to_cpp_stored()` for `{T}`
template placeholders, raw `to_cpp()` for `{cpp}`, and the resolver-bound
`lc.render_type` / `lc.render_type_stored` where the AST goes through
`TypeResolver` -- so the spelling is byte-identical by construction. An
arm whose AST spelling applies a transformation THIR does not mirror
(representational-subst adapters, literal-mangled overloads) must stay
gated to the AST path.

`expand_fi_template` itself lives in typesys (one substitution rule shared
with `gen_call_from_fi`); this module is the THIR-side import point.
"""

from __future__ import annotations

from ...typesys import expand_fi_template

__all__ = ["expand_fi_template"]
