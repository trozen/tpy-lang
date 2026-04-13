# log() macro should error when called from a zero-param function.
from log_macro import log


def no_params() -> None:
    log(f"oops")  # tpyc: error(/no parameters/)

def main() -> None:
    pass

main()
