# A @nocopy outer name read in a comprehension element is refused without the
# auto-move remedy: the element runs once per item, so it is never a last use.
from tplib.box import Box


def main() -> None:
    b = Box([1])
    ls = [b for _ in range(2)]  # tpyc: error(/into owned storage \(use Own\[\.\.\.\] to transfer ownership, or copy\(\) if a copy is truly intended\)$/)
    print(len(ls))


main()
