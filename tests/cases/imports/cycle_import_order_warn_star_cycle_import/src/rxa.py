# `rxa` binds E below its cycle import, by an import of `rxc`, which is in
# the cycle too: moved above, it would start `rxb` as early.
import rxb  # noqa: F401
from rxc import E  # noqa: F401
