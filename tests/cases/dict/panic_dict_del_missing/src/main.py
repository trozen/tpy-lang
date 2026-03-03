# del d[key] panics on missing key
from tpy import Int32

def main() -> None:
    d = {"a": Int32(1)}
    del d["missing"]

main()
