# Cycle peer re-exports a function defined OUTSIDE the cycle, reached
# through another cycle peer. main pulls `cee` via `a`; a re-exports
# cee from cycle peer b; b imports cee from c (a non-cycle module).
#
# Before the cycle re-export pre-pop pass, this failed at sema with
# "'cee' not found in module 'b'": a's bind_imports ran before b's,
# and b's per-kind dicts were still empty for cee. The fallback path
# in _register_user_module_import now consults b's pre-populated
# attribute table and registers the function chain-walked to its
# ultimate source (c).
from a import aye, cee

print(aye())
print(cee())
