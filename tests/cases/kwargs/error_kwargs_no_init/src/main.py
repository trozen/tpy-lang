# Error: kwargs on record without __init__ -- refused as positional
# arguments are, with one message.

class Bare:
    x: int
    y: int

def main() -> None:
    b = Bare(x=1, y=2)  # tpyc: error(/'Bare' has no __init__; use @dataclass/)

main()
