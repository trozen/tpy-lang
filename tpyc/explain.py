"""`tpyc --explain-send T` / `--explain-sync T`: print the structural
Send/Sync derivation for a named type (docs/SEND_SYNC_DESIGN.md OQ6).

Resolves the type expression through the entry module's real TypeResolver --
so user records, imports, and generic forms (`list[Order]`, `Send[Pet]`)
resolve exactly as they would in an annotation -- then renders the
why-not chain via the shared walker.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .compilation_context import activate_compiler
from .sema.send_chain import why_not_send, why_not_sync, render_chain

if TYPE_CHECKING:
    from .compiler import Compiler, CompiledModule


def explain_send_sync(
    compiled_modules: list['CompiledModule'], compiler: 'Compiler',
    type_string: str, send: bool,
) -> int:
    """Resolve `type_string` and print its Send/Sync derivation. Returns an
    exit code."""
    trait = "Send" if send else "Sync"
    entry = next((m for m in compiled_modules if m.is_entry_point), None)
    if entry is None or entry.ast.resolver is None:
        print(f"--explain-{trait.lower()}: no entry module to resolve "
              f"'{type_string}' against", file=sys.stderr)
        return 1
    resolver = entry.ast.resolver
    try:
        ref = resolver.parse_type_ref(type_string)
    except SyntaxError as e:
        print(f"--explain-{trait.lower()}: cannot parse type '{type_string}': {e}",
              file=sys.stderr)
        return 1

    with activate_compiler(compiler):
        try:
            resolved = resolver.resolve(ref)
        except Exception as e:  # resolver raises a variety of error classes
            print(f"--explain-{trait.lower()}: cannot resolve type "
                  f"'{type_string}': {e}", file=sys.stderr)
            return 1
        holds = resolved.is_send() if send else resolved.is_sync()
        if holds:
            print(f"{resolved} is {trait}")
            return 0
        chain = why_not_send(resolved) if send else why_not_sync(resolved)
        print(render_chain(chain, send) if chain is not None
              else f"{resolved} is not {trait}")
        return 0
