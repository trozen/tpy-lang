# Ext-only: a Final constant whose type does not cross the boundary (Char) is
# not part of the exposed surface, so the compiled module lacks it -- whereas the
# plain Python source binds it as an ordinary attribute. Checked only against the
# .so, never in cpy-parity (where TAG exists).
import constants

assert not hasattr(constants, "TAG"), "Char Final must not be exposed"

# The snapshot is a plain module attribute: Final is a type-checker hint, so
# (as in any Python module) the attribute can be reassigned at runtime, matching
# CPython. The original constant in the compiled code is unaffected.
constants.MAX_SIZE = 999
assert constants.MAX_SIZE == 999
