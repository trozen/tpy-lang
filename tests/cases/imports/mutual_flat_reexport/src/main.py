# Universal re-export through a flat (non-facade) intermediate.
# Phase 4 of the per-module attribute table refactor enables this:
# `from b import Cls` works even though `b` is a regular module that
# itself does `from c import Cls`.
from b import Cls

x = Cls(42)
print(x.val)
