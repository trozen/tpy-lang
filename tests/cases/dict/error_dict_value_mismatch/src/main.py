# Mixed value types in dict literal should error
from tpy import int32

def main() -> None:
    d = {"a": int32(1), "b": "hello"}  # tpyc: error(/mixed value types/)

main()
