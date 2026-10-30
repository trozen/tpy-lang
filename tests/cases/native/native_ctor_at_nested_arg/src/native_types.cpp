#include "native_types.hpp"

namespace mylog {
NR::NR(int32_t x) : x(x) {}

int32_t use_nr(const NR& r) {
    return r.x * 10;
}
}
