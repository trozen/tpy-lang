# None passed to record constructor with value-type optional param
# must generate std::nullopt (not nullptr) since the C++ param is std::optional<T>.
from tpy import Int32

class Holder:
    value: Int32 | None

    def __init__(self, value: Int32 | None):
        self.value = value

def main():
    h1 = Holder(None)
    print(h1.value is None)

    h2 = Holder(Int32(42))
    print(h2.value)

main()
