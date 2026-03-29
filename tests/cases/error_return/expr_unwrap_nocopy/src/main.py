# Expression-level error_return unwrap with @nocopy types.
# Verifies move semantics: @nocopy types have deleted copy constructors,
# so this only compiles if the unwrap uses std::move.
from tpy import error_return, ControlFlow, nocopy, Own

class E(Exception, ControlFlow):
    pass

@nocopy
class Data:
    value: int

    def __init__(self, v: int):
        self.value = v

@error_return(E)
def make_data(v: int) -> Own[Data]:
    if v < 0:
        raise E
    return Data(v)

# Field access on unwrapped @nocopy result
@error_return(E)
def get_value(v: int) -> int:
    return make_data(v).value

def main() -> None:
    try:
        print(get_value(42))
    except E:
        print("error")

    try:
        print(get_value(-1))
    except E:
        print("caught")

main()
