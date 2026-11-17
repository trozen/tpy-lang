# `from apk import *` loads the listed submodules in this order: `b` first,
# so b's from-import of `apk.a` runs a to its end. The two warnings are
# BUGS.md#package-all-lists-submodule-false-warning.
__all__ = ["b", "a"]  # tpyc: warning(/__all__ lists 'b' which is not defined/) warning(/__all__ lists 'a' which is not defined/)
