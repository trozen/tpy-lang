# An except clause naming a module member that is no class (a function)
# says so, rather than that the member is missing.
import mx


def main() -> None:
    try:
        print("body", mx.f())
    except mx.f:  # tpyc: error(/'mx.f' is not an exception class/)
        print("caught")


main()
