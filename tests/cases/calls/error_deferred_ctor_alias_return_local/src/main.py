# A local returned by reference dangles (LANGUAGE_FEATURES "Own[T]"): a
# deferred `Sack()` is refused as its explicit `Sack[int32]()` twin is.
from tpy import int32
from bags import Bag
from bags import Bag as Sack


class H:
    def make(self) -> Bag[int32]:
        s = Sack()
        s.put(6)
        return s  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(len(H().make().items))


main()
