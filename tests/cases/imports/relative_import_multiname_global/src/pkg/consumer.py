# `from . import b, a` (multiple submodules, one statement) must run each
# submodule's __tpy_init deterministically; reads a module-global from each.
from . import bmod, amod


def check() -> None:
    print(amod.a_first())
    print(bmod.b_first())
