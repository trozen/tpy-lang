# Returning an f-string as a StrView is rejected: an f-string materializes a
# fresh std::string temporary, so a view of it dangles at return.
from tpy import StrView


def ret_fstring(n: int) -> StrView:
    return f"prefix-{n}-suffix"  # tpyc: error(/StrView referencing a local or temporary/)


def main() -> None:
    print(ret_fstring(7))


main()
