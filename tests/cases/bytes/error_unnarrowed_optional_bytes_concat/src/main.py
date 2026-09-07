# `bytes + Optional[bytes]` with the optional never proven non-None: the
# concat has no form for an operand that still needs a runtime check, which
# is also what the optional-access warning on that line reports.
from tpy import Int32


def build(body: bytes | None) -> Int32:
    data = b"head"
    # `body` is unnarrowed at the concat.
    data = data + body  # tpyc: error(/stmt\.var_decl:binop\.shape\.\+/)
    return len(data)


def main() -> None:
    print(build(b"x"))


main()
