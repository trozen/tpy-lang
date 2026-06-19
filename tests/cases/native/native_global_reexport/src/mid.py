# Re-exports leaf's native_global (GLOBAL_VAL) and plain Final (NORMAL_VAL)
# through its own header. The native_global must NOT get a header re-export
# alias (it has no `::leaf::GLOBAL_VAL` symbol; use sites resolve the native
# name directly), while the plain Final still does.
from tpy import Int32
from leaf import GLOBAL_VAL, NORMAL_VAL


def use_global() -> Int32:
    return GLOBAL_VAL


def use_normal() -> Int32:
    return NORMAL_VAL
