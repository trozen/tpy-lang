# max() / min() over an iterable of class instances is refused: the result
# would be a copy of the winning element, where Python returns the element
# itself (docs/LANGUAGE_FEATURES.md, the builtins section;
# BUGS.md#min-max-key-result-copies). Value-type elements are covered by
# builtins/min_max_iterable.
class Player:
    score: int

    def __init__(self, score: int) -> None:
        self.score = score

    def __lt__(self, other: "Player") -> bool:
        return self.score < other.score


def main() -> None:
    ps = [Player(3), Player(7)]
    best = max(ps)  # tpyc: error(/Type 'Player' does not satisfy 'ComparableValue' required by 'max': 'Player' is not a value type/)
    best.score = 100
    print(ps[1].score)


main()
