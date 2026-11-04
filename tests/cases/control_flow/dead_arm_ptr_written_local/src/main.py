# A local bound once to a literal is no constant once a call takes its
# address: the callee writes it, so a test on it stays a run-time test.
from tpy import Ptr, int32, take_ptr
from tpy.unsafe import unsafe_store


def setp(p: Ptr[int32]) -> None:
    unsafe_store(p, 0, 5)


def passed_to_call() -> str:
    e: int32 = 0
    setp(take_ptr(e))
    # Decided on the bound literal, this would print "zero".
    if e == 0:  # tpyc: ok
        return "zero"
    return "set"


def main() -> None:
    print("passed", passed_to_call())


main()
