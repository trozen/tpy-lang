import itertools
from tpy import int32
def f(n: int32) -> int32:
    match n:
        case 1 if len(list(itertools.islice(itertools.count(), 4))) > 0:
            return 1
        case _:
            return 0
def main() -> None:
    print(f(1))
main()
