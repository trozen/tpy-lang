# Regression: `is` / `is not` between two Rc handles is rejected because TPy's
# identity-comparison rules currently scope `is` to None / enum / bool only.
# This pins the rejection so a future relaxation (or accidental wider
# acceptance) is noticed; cell-pointer identity for Rc handles is tracked as a
# capability gap in BUGS.md ("No way to express cell-pointer identity ...").
from tplib import Rc


def main() -> None:
    a = Rc.new(10)
    b = a.clone()  # same cell as a
    if a is b:  # tpyc: error(/'is' \/ 'is not' can only compare Optional\/Ptr\/union types with None/)
        print("same")


main()
