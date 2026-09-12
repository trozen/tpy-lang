# Regression: `Ptr[CrossModule @dynamic Protocol]` as a record field type.
# The pointee qualification goes through `NominalType.to_cpp()` -> the
# `_native_cpp_names` map (populated per emit-module in
# `tpyc/codegen_cpp/generator.py`'s imported-protocol loop). If the loop
# regressed, the field type would emit as bare `Counter*` instead of
# `::tpyapp::pet::Counter*` and fail to compile.
from tpy import int32, Ptr, nocopy
from pet import Counter


@nocopy
class Tally(Counter):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self, by: int32) -> None:
        self.n += by

    def value(self) -> int32:
        return self.n


@nocopy
class Notifier:
    target: Ptr[Counter]

    def __init__(self) -> None:
        self.target = None

    def aim(self, p: Ptr[Counter]) -> None:
        self.target = p

    def trigger(self, by: int32) -> None:
        if self.target is None:
            return
        self.target.bump(by)


def main() -> None:
    t = Tally()
    n = Notifier()
    n.aim(t)  # tpyc: ok  -- record -> Ptr[cross-module Counter] upcast
    n.trigger(5)
    n.trigger(7)
    print(t.value())


main()
