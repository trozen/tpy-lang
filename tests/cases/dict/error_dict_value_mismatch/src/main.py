# Mixed value types in dict literal should error
from tpy import Int32

def main() -> None:
    d = {"a": Int32(1), "b": "hello"}  # tpyc: error(/mixed value types/)

main()
