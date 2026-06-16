# A deferred (post-sema) function macro raises ctx.error from its callback when
# the inferred return type isn't Int32 -- here `report` returns a str.
from checkmod import require_int_return


@require_int_return
def report() -> str:  # tpyc: error(/expected an Int32 return/)
    return "nope"


def main() -> None:
    print(report())


main()
