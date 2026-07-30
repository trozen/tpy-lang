# @auto_readonly tracks receiver const-ness; a classmethod has no receiver.
from tpy import Int32, auto_readonly


class P:
    @auto_readonly  # tpyc: error(/@auto_readonly cannot be combined with @classmethod/)
    @classmethod
    def get(cls) -> Int32:
        return 1


def main() -> None:
    print(P.get())


main()
