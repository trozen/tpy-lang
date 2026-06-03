# A @function_macro deduces the type of string-literal bool locals: it retypes
# each `x = "true"/"false"` local as bool and rewrites the RHS to a bool
# literal, so the mutated body type-checks and runs. Proves annotate_local +
# replace_expr end-to-end.
from boolmod import bool_locals


@bool_locals
def pick(use_first: bool) -> bool:
    yes = "true"
    no = "false"
    return yes if use_first else no


def main() -> None:
    print(1 if pick(True) else 0)
    print(1 if pick(False) else 0)


main()
