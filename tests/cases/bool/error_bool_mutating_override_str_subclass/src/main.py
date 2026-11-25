# A str subclass whose __bool__ mutates is not Truthy (Truthy's __bool__ is
# readonly), so bool() refuses it rather than running the inherited test -- a
# current limitation, BUGS.md#mutating-dunder-const-wrapper.
from tpy import readonly


class Loud(str):  # tpyc: warning(/hides 'str.__bool__'/)
    @readonly(False)
    def __bool__(self) -> bool:
        print("called")
        return True


def main() -> None:
    s = Loud()
    print(bool(s))  # tpyc: error(/bool\(\) cannot convert Loud/)


main()
