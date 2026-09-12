# Consuming dict keys via Iterable[Own[T]] constructor at last use.
# list(dict) uses own_iter_dict when dict is at last use.
from tpy import int32

def main() -> None:
    d: dict[str, int32] = {"a": 1, "b": 2, "c": 3}
    keys = list(d)
    print(keys)

main()
