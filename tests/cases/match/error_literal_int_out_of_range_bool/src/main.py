# The `bool` face of error_literal_int_out_of_range: a bool subject holds 0
# and 1, so any other int literal is an arm that can never fire. Its own case
# because the range clause is the one part of the message that differs, and
# compilation stops at the first error. CPython RUNS this program.
def classify(v: bool) -> str:
    match v:
        case 2:  # tpyc: error(/int literal pattern 2 can never match subject type 'bool', which holds 0 and 1/)
            return "two"
        case _:
            return "other"


def main() -> None:
    pass


main()
