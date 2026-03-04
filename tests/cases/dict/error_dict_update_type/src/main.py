# Wrong dict type in update() should error
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {"a": 1}
    d2: dict[str, float] = {"b": 2.0}
    d.update(d2)  # tpyc: error(/Type mismatch in argument 'other'/)

main()
