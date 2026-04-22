# dist raises ValueError on length mismatch. The seed-phase / main-loop
# try/except chains mirror sumprod; this test covers the p-longer main-loop
# branch. Seed-phase mismatch (one side empty) is structurally equivalent
# and not separately panic-tested.
import math


def main() -> None:
    math.dist([1.0, 2.0, 3.0], [4.0, 5.0])


main()
