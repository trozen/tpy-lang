# Stage 2c sema rule: a class implementing Throwable directly must instead
# extend BaseException. Throwable is the ABI protocol; BaseException is the
# user extension point (provides the `message` field that codegen's
# auto-emitted `what()` reads).
from tpy import Throwable


class Direct(Throwable):  # tpyc: error(/does not inherit BaseException/)
    pass


def main() -> None:
    pass


main()
