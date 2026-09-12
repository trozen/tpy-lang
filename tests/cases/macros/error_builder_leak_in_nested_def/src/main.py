# A tracked builder symbol captured by a nested def has no runtime
# representation -- the validator must descend into nested function
# bodies even though the generic TpyStmt.sub_bodies() walk skips them.
from tpy import int32
from _smoke_builder import Config


def main() -> int32:
    cfg = Config()

    def helper() -> None:
        cfg.add("x", "1")  # tpyc: error(/lambda, or nested def/)

    helper()
    cfg.build()
    return 0


main()
