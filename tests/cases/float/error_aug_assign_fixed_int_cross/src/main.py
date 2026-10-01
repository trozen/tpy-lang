# A declared fixed-int slot keeps its declared type: a cross-width aug-assign
# whose result is wider is refused, naming the explicit conversion
from tpy import int16, int64

def main() -> None:
    i: int16 = 2
    i += int64(3)  # tpyc: error(/'i' is declared int16 and keeps its declared type, but this '\+=' produces int64; convert explicitly: i = int16\(i \+ \.\.\.\)/)

main()
