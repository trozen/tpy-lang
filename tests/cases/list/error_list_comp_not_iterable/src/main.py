# Error: non-iterable source in comprehension
from tpy import Int32
def main() -> None:
    result = [x for x in Int32(42)]  # tpyc: error(/[Cc]annot iterate/)
main()
