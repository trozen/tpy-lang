#include "native_types.hpp"

namespace mylog {
NR::NR(int32_t x) : x(x) {}

int32_t use_nr(const NR& r) {
    return r.x * 10;
}

int32_t peek_nr(const NR& r) {
    return r.x * 1000;
}

int32_t NR::absorb(const NR& o) const {
    return x + o.x;
}

int32_t push_list(std::vector<int32_t>& xs, int32_t v) {
    xs.push_back(v);
    return static_cast<int32_t>(xs.size()) * 100 + v;
}

int32_t bump_nr(NR& r) {
    r.x += 1;
    return r.x;
}
}
