# Pins the BUGS.md gap: `__all__` listing a name the source module
# does not actually define is currently a silent drop. CPython raises
# `AttributeError: module 'lib' has no attribute 'phantom_name'` at
# star-import time. TPy compiles cleanly, real_fn works, phantom_name
# is just absent from scope -- the diagnostic is missing.
#
# When this is fixed (warning or error at the source module's
# __all__), the diag snapshot will change. Bump the snapshot together
# with the fix and retire the BUGS.md entry. Skipping CPython because
# CPython's import-time AttributeError is the behavior divergence we
# are tracking.
from lib import *

real_fn()
