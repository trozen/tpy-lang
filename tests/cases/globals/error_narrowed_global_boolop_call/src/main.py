# A call operand inside the SAME condition kills a value-type global's
# narrow for the whole condition (evaluation order is not tracked).
from tpy import int32

GO: int32 | None = None


def check() -> bool:
    return True


def stale_boolop() -> int32:
    if GO is not None and check():
        return GO  # tpyc: error(/expected int32, got int32 \| None/)
    return -1


def main() -> None:
    print(stale_boolop())


main()
