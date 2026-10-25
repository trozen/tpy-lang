# char() takes its value positionally only (`/`), as the CPython stand-in
# does: each variant says why it refuses the keyword (pinned in diag.txt).
from tpy import char


def main() -> None:
    print(char(x=65))  # tpyc: error(/No matching overload for 'char' with argument types \(x=int\)/)


main()
