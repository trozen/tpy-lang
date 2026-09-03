"""Type-arg spelling helpers for the generics frontier.

The invariant: every type spelled at a use site goes through one of these
renderers -- raw `to_cpp_stored()` for `{T}` template placeholders, raw
`to_cpp()` for `{cpp}`, and the resolver-bound `lc.render_type` /
`lc.render_type_stored` where the type needs the `TypeResolver`. A shape
whose spelling would need a transformation these do not apply
(representational-subst adapters, literal-mangled overloads) is not
lowered.

`expand_fi_template` itself lives in typesys (one substitution rule shared
across call sites); this module is the THIR-side import point.
"""

from __future__ import annotations

from ...typesys import expand_fi_template

__all__ = ["expand_fi_template"]
