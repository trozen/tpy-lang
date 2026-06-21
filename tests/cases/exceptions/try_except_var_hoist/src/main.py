# A var assigned on all paths of a try/except (try body + every handler) and
# read after the block must hoist to the outer scope. Both arms exercised.
from tpy import Int32


def risky(fail: bool) -> Int32:
    if fail:
        raise OSError("boom")
    return 5


def run(fail: bool) -> Int32:
    try:
        x = risky(fail)
    except OSError:
        x = -1
    return x


def main() -> None:
    print(run(False))
    print(run(True))


main()
