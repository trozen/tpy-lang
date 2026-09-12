# del d[key] panics on missing key
from tpy import int32

def main() -> None:
    d = {"a": int32(1)}
    del d["missing"]

main()
