# A concrete container *variable* (list[int32]) passed where list[Box] (Box a
# recursive union) is expected: std containers have no element-converting
# constructor, so TPy rejects it with a clean diagnostic (rather than emitting
# an ill-formed C++ deep conversion). This is the direct guard path -- the
# message is swallowed when it fires inside a wider union match (see the
# json.dumps concrete-container case), but surfaces here at a list[Box] param.
from boxlib import sink
from tpy import int32


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    sink(xs)  # tpyc: error(/cannot convert list\[int32\] element-wise into the expected recursive-union type/)


main()
