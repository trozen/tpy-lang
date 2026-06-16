# A post-sema deferred macro that calls annotate_local must error: inference
# has already run, so there is nothing left to annotate.
from annotmod import bad_annotate
from tpy import Int32


@bad_annotate
def f() -> Int32:  # tpyc: error(/annotate_local is not available post-sema/)
    x = 1
    return x


def main() -> None:
    print(f())


main()
