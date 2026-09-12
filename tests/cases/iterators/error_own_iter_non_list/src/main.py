# own_iter() only accepts list[T] arguments.
from tpy import int32, own_iter

def test() -> None:
    d: dict[str, int32] = {"a": 1}
    for k in own_iter(d):  # tpyc: error(/own_iter\(\) currently only supports list/)
        print(k)
