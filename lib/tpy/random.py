# tpy: cpp_namespace("tpystd::random")
# tpy: include("<cstdlib>")
from tpy.extern import native
from tpy import Int32

@native("std::rand")
def _rand() -> Int32: ...

@native("std::srand")
def _srand(n: Int32) -> None: ...

# TODO: use std::mt19937 (Mersenne Twister, same as CPython) instead of std::rand.
# Needs shared engine state between random() and seed(), which requires either
# a C++ header or a way to store C++ objects as module-level TPy state.
# RAND_MAX is 2^31-1 on Linux/macOS, 32767 on Windows MSVC.
_RAND_MAX: float = 2147483647.0

def random() -> float:
    return float(_rand()) / _RAND_MAX

def seed(n: Int32) -> None:
    _srand(n)
