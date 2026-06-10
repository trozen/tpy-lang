# Entry point pulling both cycle members so the SCC is compiled and the
# completeness gate runs over a's overloaded `measure`.
from a import measure
from b import B


def main() -> None:
    print(measure(5).payload)


main()
