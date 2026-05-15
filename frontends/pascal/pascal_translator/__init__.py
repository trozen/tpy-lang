"""Pascal frontend translator package (plugin internals).

This package holds the Pascal lexer, parser, AST, and Pascal-AST ->
Frontend-IR translator. The `pascal_frontend.py` entry module loads it
and exposes a `FrontendPlugin` subclass as `PLUGIN`.

The runtime helpers consumed by translated Pascal programs live in a
separate `pascal/` package alongside this one, so their canonical
module name `pascal.runtime.X` resolves cleanly without colliding with
the translator's own modules.
"""
