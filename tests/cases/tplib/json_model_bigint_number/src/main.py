# Regression: @model round-trips `int` (BigInt) fields as bare JSON numbers,
# not quoted strings -- matching stdlib json.dumps/loads and CPython. A
# bare-number payload must parse, to_json must emit an unquoted number, and
# arbitrary precision (values beyond int64) must survive a round-trip.
from tplib.json.model import model

@model
class Repo:
    name: str
    stars: int

def main() -> None:
    # Bare JSON number into an `int` field -- the GitHub-API shape that used to
    # panic with `expected '"'`.
    r = Repo.from_json('{"name": "cpython", "stars": 73404}')
    print(r.name, r.stars)

    # to_json emits a bare number, not a quoted string.
    print(Repo("x", 42).to_json())

    # Arbitrary precision survives a round-trip (value well beyond int64).
    big = Repo.from_json('{"name": "big", "stars": 123456789012345678901234567890}')
    print(big.stars)
    print(Repo.from_json(big.to_json()).stars == big.stars)

    # A negative BigInt reads and round-trips as a bare (signed) number.
    neg = Repo.from_json('{"name": "neg", "stars": -987654321098765432109876543210}')
    print(neg.stars)
    print(Repo.from_json(neg.to_json()).stars == neg.stars)

main()
