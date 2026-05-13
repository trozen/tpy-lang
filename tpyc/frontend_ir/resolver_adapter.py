"""Parser-shaped adapter so the real `TypeResolver` works on
plugin-lowered modules unchanged.

`TypeResolver` reads a handful of attributes off its owning `Parser`
(see the TypeResolver docstring for the contract): `registry`,
`_imports`, `_type_param_scope`, `_module_class_names`,
`_module_type_alias_names`, `_module_aliases`, `_reverse_module_aliases`,
`_nested_type_scope`, `_local_defs`, `_bare_module_imports`. Plus
three methods: `_resolve_type_name`, `_raise_unresolved_import_error`,
`_warn_at_loc`.

`_PluginParserAdapter` implements that contract for plugin modules
without instantiating a real `Parser`. Plugin lowering populates it
from the IR (imports, module name, directives); the rest of the
parser-internal bookkeeping is empty-but-correct frozensets/dicts.
"""

from __future__ import annotations

from typing import Iterable

from ..parse import SourceLocation
from ..parse.imports import (
    ImportProcessor, get_builtins_exports, get_tpy_exports, get_typing_exports,
)
from ..parse.nodes import ParseError
from ..parse.type_resolver import TypeResolver
from ..typesys import TypeRegistry


class _PluginParserAdapter:
    """Parser-shaped object consumed by `TypeResolver`.

    Constructed once per plugin-lowered module. Lowering fills the
    import-related attributes from the IR; the parser-only bookkeeping
    fields stay at safe defaults (empty frozensets, empty dicts) because
    plugin modules have no parse-time class scan, no Python `ast` to
    consult, and no `# tpy:` directive scanning.
    """

    def __init__(
        self,
        module_name: str,
        is_entry_point: bool,
        shared_modules: 'dict | None' = None,
    ) -> None:
        self.registry = TypeRegistry(shared_modules=shared_modules)
        self._imports = ImportProcessor(self._warn, module_name=module_name)
        self._imports.imports = {}
        self._module_name = module_name
        self._is_entry_point = is_entry_point

        # Parser-only bookkeeping. Plugin lowering produces no `ast` to
        # walk, so all of these stay empty -- TypeResolver still consults
        # them but they correctly answer "nothing here" in every case.
        self._local_defs: frozenset[str] = frozenset()
        self._module_class_names: frozenset[str] = frozenset()
        self._module_type_alias_names: frozenset[str] = frozenset()
        self._module_aliases: dict[str, str] = {}
        self._reverse_module_aliases: dict[str, str] = {}
        self._bare_module_imports: set[str] = set()
        self._nested_type_scope: dict[str, str] = {}
        self._type_param_scope: dict[str, object] | None = None

        # Parser-side warnings collected by `_warn_at_loc`. Plugin
        # lowering does not currently surface these back, but holding
        # them avoids reaching into a `None`.
        self._warnings: list[tuple[str, SourceLocation | None]] = []

    # ------------------------------------------------------------------
    # Methods TypeResolver calls on the parser

    def _resolve_type_name(self, local_name: str) -> tuple[str, str] | None:
        """Mirror `Parser._resolve_type_name` for plugin-lowered modules.

        Plugin modules have no `_local_defs` (no Python ast to scan) and
        no locally-registered builtins, so the parser version's first
        branch and last branch are no-ops here. The remaining logic --
        consult the import name index, fall back to ambient builtins --
        applies unchanged.
        """
        source = self._imports.get_import_source(local_name)
        if source:
            return source
        # `get_builtins_exports` is the Python `builtins` surface (`int`,
        # `str`, ...). Plugin modules can reference these without an
        # explicit import, exactly as Python source can.
        if local_name in get_builtins_exports():
            return ("builtins", local_name)
        return None

    def _raise_unresolved_import_error(
        self,
        raw_name: str,
        node: object | None = None,
        *,
        loc: SourceLocation | None = None,
    ) -> None:
        """Mirror the parser's import-hint diagnostic."""
        if raw_name in get_typing_exports():
            raise ParseError(
                f"'{raw_name}' requires: from typing import {raw_name}",
                loc=loc,
            )
        if raw_name in get_tpy_exports():
            raise ParseError(
                f"'{raw_name}' requires: from tpy import {raw_name}",
                loc=loc,
            )

    def _warn_at_loc(self, message: str, loc: SourceLocation | None) -> None:
        self._warnings.append((message, loc))

    # Helper used as ImportProcessor's `warn_fn`.
    def _warn(self, message: str, node: object | None = None,
              *, loc: SourceLocation | None = None) -> None:
        self._warnings.append((message, loc))


def make_plugin_resolver(
    module_name: str,
    is_entry_point: bool = False,
    shared_modules: 'dict | None' = None,
    *,
    imports: 'dict[str, set[tuple[str, str]] | None] | None' = None,
    name_index: 'Iterable[tuple[str, str, str]]' = (),
) -> TypeResolver:
    """Build a `TypeResolver` for a plugin-lowered module.

    `imports` and `name_index` mirror the parser's two-tier import
    bookkeeping:
      - `imports`: module-name -> set of `(original, local)` tuples
        (or None for whole-module imports). Mirrors `TpyModule.imports`.
      - `name_index`: each entry is `(local_name, source_module,
        original_name)`. Mirrors what `ImportProcessor._index_import`
        would have produced during parsing.

    The two tables exist for distinct lookup needs in TypeResolver --
    `imports` is consulted for whole-module imports while resolving
    qualified names; `name_index` is consulted for the bare-name path.
    """
    adapter = _PluginParserAdapter(
        module_name=module_name,
        is_entry_point=is_entry_point,
        shared_modules=shared_modules,
    )
    if imports is not None:
        adapter._imports.imports = imports
    for local, src_mod, orig in name_index:
        adapter._imports.index_imported_name(local, src_mod, orig)
    return TypeResolver(adapter)
