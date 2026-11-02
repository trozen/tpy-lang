# The dropped-coroutine check reads the callee as the call does: an async
# def's name the function later rebinds is the function's local there, so the
# call statement is CPython's UnboundLocalError, not a dropped coroutine.
from tpy import int32


async def work() -> int32:
    return 1


def main() -> None:
    # The callee read the later `work = 5` makes local.
    work()  # tpyc: error(/'work' is read before it is bound on line 14: .* does not see the module's 'work'/)
    work = 5
    print(work)


main()
