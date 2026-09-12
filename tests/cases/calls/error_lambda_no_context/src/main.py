# Lambda without Fn type context is an error.
from tpy import int32

def main() -> None:
    f = lambda x: x + 1  # tpyc: error(/[Ll]ambda parameter types cannot be inferred/)

main()
