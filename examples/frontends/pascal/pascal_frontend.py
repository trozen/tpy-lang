"""Turbo Pascal frontend plugin for TurboPython.

Entry module: exposes `PLUGIN` for the compiler to pick up via
`--dsl-plugin path/to/pascal_frontend.py`. See
`examples/frontends/pascal/DESIGN.md` for the milestone roadmap.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tpyc.diagnostics import Diagnostic, DiagnosticLevel
from tpyc.frontend_plugin import FrontendOutput, FrontendPlugin, WorkspaceContext


# Allow importing the sibling `pascal_translator` package when this file
# is loaded by absolute path (the common case for
# `--dsl-plugin path/to/pascal_frontend.py`). The package lives next to
# this entry module.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from pascal_translator import parser as _parser  # noqa: E402
from pascal_translator import translate as _translate  # noqa: E402
from pascal_translator.lexer import LexError  # noqa: E402
from pascal_translator.parser import ParseError  # noqa: E402
from pascal_translator.translate import read_pascal_source  # noqa: E402


class PascalFrontend(FrontendPlugin):
    api_version = 1
    name = "pascal"
    extensions = (".pas", ".pp")
    decorator_manifest = ()       # Pascal has no decorators in M1

    def library_paths(self) -> tuple[Path, ...]:
        # Two dirs: the plugin root (so `pascal.runtime.io` and
        # similar dotted-stdlib modules resolve as `pascal/runtime/
        # io.py`) and `pascal/lib/` (so bare `uses Crt`-style imports
        # resolve to `pascal/lib/crt.py`). Both are needed; surfacing
        # them through the FrontendPlugin hook means users only have
        # to pass `--dsl-plugin pascal_frontend.py` -- no extra `-L`.
        return (_THIS_DIR, _THIS_DIR / "pascal" / "lib")

    def parse(self, ctx: WorkspaceContext,
              module_name: str, file_path: Path) -> FrontendOutput:
        # Use the encoding-tolerant reader rather than ctx.read_file
        # (which is UTF-8 only). TP7 source typically lives in a
        # DOS / Windows-Eastern-European code page; reading it as
        # UTF-8 raises on the first accented character in a
        # comment or string literal.
        source = read_pascal_source(file_path)
        try:
            program = _parser.parse(source, file_path)
        except (LexError, ParseError) as e:
            from tpyc.parse import SourceLocation
            diag = Diagnostic(
                level=DiagnosticLevel.ERROR,
                message=str(e).split(": ", 1)[-1],
                loc=SourceLocation(line=e.line, column=max(0, e.col - 1),
                                   file=str(file_path)),
            )
            # Return a valid (empty) module so lowering can still wrap
            # the diagnostic with category=PLUGIN_REPORTED.
            from tpyc.frontend_ir import FrontendModule
            return FrontendOutput(
                module=FrontendModule(
                    qname=module_name, source_language="pascal",
                    source_lines=tuple(source.splitlines()),
                ),
                diagnostics=[diag],
            )
        module, diags = _translate.translate(program, module_name, ctx)
        return FrontendOutput(module=module, diagnostics=diags)


PLUGIN = PascalFrontend
