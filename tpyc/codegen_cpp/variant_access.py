"""Codegen helper for std::variant access patterns.

TurboPython unions lower to one of four C++ shapes:

- **Value variant**: `std::variant<A, B, ...>` -- members are value-typed,
  variant stores them by value. Field / container / Own slot form.
- **Pointer variant**: `std::variant<A*, B*, ...>` -- members are non-value
  reference types; the variant carries borrowed pointers. Param / local /
  return form.
- **Wrapper struct**: `struct Tree { std::variant<...> value; }` -- emitted
  for recursive union aliases (`type Tree = int | list[Tree]`) because
  a plain `using` alias isn't forward-declarable in C++. The inner
  `std::variant` is accessed via `.value`.
- **Optional-wrapped variant**: `std::optional<Tree>` where `Tree` is a
  wrapper-struct alias, narrowed from `Tree | None`. The deref'd value
  is itself a wrapper struct, so its `std::variant` is reached via
  `(*opt).value`.

Each access site -- `std::holds_alternative<...>(...)`, `std::get<...>(...)`,
`.index()` switch dispatch -- has to make four decisions: deref prefix
(`*` for ptr-variant), type suffix (`*` for ptr-variant), `.value`
indirection (wrapper struct), and const propagation (`const T*` for
const-indirect locals). `VariantAccess` captures those decisions once at
construction and emits the right expressions through its methods, so
call sites stop re-deriving the same shape facts inline.

This module is the canonical source for variant access strings. Direct
`std::get` / `std::holds_alternative` formatting elsewhere in codegen
should be migrated here over time.
"""

from __future__ import annotations
from dataclasses import dataclass

from ..typesys import TpyType, OptionalType, AliasRef, unwrap_qualifiers, unwrap_ref_type


def _needs_value_indirection(typ: TpyType | None) -> bool:
    """True if the type's C++ form is a wrapper struct whose `std::variant`
    is reached via `.value`. Covers recursive-union wrappers and
    OptionalType wrapping a recursive-alias placeholder (the narrowed
    deref'd value is itself a wrapper struct). Qualifiers (`readonly`,
    `Own`, `Ref`) are peeled before testing -- a `readonly[Tree]` param
    still resolves to its wrapper struct at the variant access site."""
    if typ is None:
        return False
    peeled = unwrap_ref_type(unwrap_qualifiers(typ))
    if peeled.needs_wrapper():
        return True
    if isinstance(peeled, OptionalType) and isinstance(peeled.inner, AliasRef):
        return True
    return False


@dataclass
class VariantAccess:
    """Codegen helper for emitting std::variant access patterns.

    Construct once per access site with the base C++ expression, its
    TPy type, and the shape flags (`is_ptr_variant`, `is_const`), then
    call `index_expr()`, `holds(member)`, `get_by_type(member)`, or
    `get_by_index(idx)` to produce the C++ access string.
    """
    base_expr: str              # C++ expression that yields the variant or its wrapper
    typ: TpyType | None         # TPy type of `base_expr`; None disables wrapper indirection
    is_ptr_variant: bool        # runtime form is `std::variant<T*, U*, ...>`
    is_const: bool = False      # const propagation for ptr-variant get (`std::get<const T*>(...)`)

    # --- Access expressions --------------------------------------------

    @property
    def variant_expr(self) -> str:
        """C++ expression yielding the underlying `std::variant`.

        Adds `.value` when the base type is a wrapper struct (recursive
        alias) or an Optional wrapping a recursive-alias placeholder.
        """
        if _needs_value_indirection(self.typ):
            return f"{self.base_expr}.value"
        return self.base_expr

    def index_expr(self) -> str:
        """`variant.index()` -- for switch dispatch on the active tag."""
        return f"{self.variant_expr}.index()"

    def holds(self, member_cpp: str) -> str:
        """`std::holds_alternative<member>(variant)` -- type test for a
        specific alternative. Adds `*` suffix for ptr-variant and `const`
        prefix for const-indirect borrows."""
        type_arg = self._type_arg(member_cpp)
        return f"std::holds_alternative<{type_arg}>({self.variant_expr})"

    def get_by_type(self, member_cpp: str, *, lvalue: bool = False) -> str:
        """`std::get<member>(variant)` extracting the typed alternative.

        For ptr-variant, the result is dereferenced so the form is the
        same value shape as for value-variant. `lvalue=False` (default)
        wraps in outer parens for safe embedding in larger expressions
        (`(*std::get<T*>(v))`). `lvalue=True` omits the parens, suitable
        for direct binding sites like `auto& local = expr;`."""
        type_arg = self._type_arg(member_cpp)
        if self.is_ptr_variant:
            inner = f"*std::get<{type_arg}>({self.variant_expr})"
            return inner if lvalue else f"({inner})"
        return f"std::get<{type_arg}>({self.variant_expr})"

    def get_by_index(self, idx: int) -> str:
        """`std::get<I>(variant)` -- match-arm extraction by tag index.

        For ptr-variant: emits `*std::get<I>(variant)` (without outer
        parens; match-arm sites consume the expression as an lvalue and
        bind via `auto&`)."""
        if self.is_ptr_variant:
            return f"*std::get<{idx}>({self.variant_expr})"
        return f"std::get<{idx}>({self.variant_expr})"

    # Boundary conversions (`::tpy::to_ptr_variant` / `to_const_ptr_variant`
    # / `to_value_variant`) are not exposed here because every call site
    # has different surrounding context (template arg for value form,
    # mutability for ptr form). Keeping them open-coded at the call site
    # has proven clearer than a partial-uniformity API.

    # --- Internal ------------------------------------------------------

    def _type_arg(self, member_cpp: str) -> str:
        """Build the template argument for `std::get<...>` /
        `std::holds_alternative<...>`. Adds `*` suffix and `const` prefix
        when the runtime form is ptr-variant. `std::monostate` (the None
        tag) is form-invariant -- ptr-variants carry it verbatim as a
        monostate alternative, not a `std::monostate*` -- so it passes
        through unchanged."""
        if self.is_ptr_variant and member_cpp != "std::monostate":
            const = "const " if self.is_const else ""
            return f"{const}{member_cpp}*"
        return member_cpp
