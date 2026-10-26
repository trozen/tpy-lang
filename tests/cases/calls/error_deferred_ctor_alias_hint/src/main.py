# An instance no use completes (LANGUAGE_FEATURES "Deferred generic instance
# inference"): the hint names the class through its import alias.
from bags import Bag as Sack


class H:
    def make(self) -> None:
        s = Sack()  # tpyc: error(/add explicit type args \(e\.g\., Sack\[T\]\(\)\)/)
        print("made")


def main() -> None:
    H().make()


main()
