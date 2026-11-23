# Module-qualified native free calls lend a fresh argument as the bare
# spelling does (`::mylog::bump_nr(::tpy::as_mut_lvalue(::mylog::NR(x)))`).
from tpy import int32
import nr_api as m
from nr_api import NR


def qualified(x: int32) -> int32:
    # a construction at a module-qualified native's `NR&` parameter
    return m.bump_nr(NR(x))  # tpyc: ok


def qualified_list() -> int32:
    # a zero-argument construction at a module-qualified native's
    # `std::vector&` parameter
    return m.push_list(list(), 9)  # tpyc: ok


def main() -> None:
    print("qualified", qualified(6), qualified_list())
