# A body-less producer of a fresh list, called bare and module-qualified.
from tpy import Own, int32
from tpy.extern import cpp_template


@cpp_template("std::vector<int32_t>{{1, 2, 3}}")
def make_vec() -> Own[list[int32]]: ...
