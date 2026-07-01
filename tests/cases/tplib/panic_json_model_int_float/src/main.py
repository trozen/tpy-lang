# A non-integer JSON number (1.5) in an `int`/BigInt @model field fails at parse
# time: read_number_raw reads the float lexeme, then int() rejects it -- parity
# with CPython int("1.5") raising ValueError. Pins the failure mode after the
# switch to bare-number BigInt (the old string encoding rejected this earlier,
# with an opaque `expected '"'`).
from tplib.json.model import model

@model
class M:
    n: int

def main() -> None:
    m = M.from_json('{"n": 1.5}')
    print(m.n)

main()
