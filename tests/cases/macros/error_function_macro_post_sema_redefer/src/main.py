# A deferred (post-sema) callback that itself calls defer_until_sema_complete
# is a hard error -- the drain runs once, it does not loop.
from redefermod import bad_redefer
from tpy import int32


@bad_redefer
def f() -> int32:  # tpyc: error(/registered another deferred callback/)
    return 1


def main() -> None:
    print(f())


main()
