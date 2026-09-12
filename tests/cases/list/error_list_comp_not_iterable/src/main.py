# Error: non-iterable source in comprehension
from tpy import int32
def main() -> None:
    result = [x for x in int32(42)]  # tpyc: error(/[Cc]annot iterate/)
main()
