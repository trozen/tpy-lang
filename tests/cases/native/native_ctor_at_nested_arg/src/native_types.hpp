#pragma once
#include <cstdint>
#include <vector>

namespace mylog {
struct NR {
    int32_t x;
    explicit NR(int32_t x);
    int32_t absorb(const NR& o) const;
};

int32_t use_nr(const NR& r);
int32_t peek_nr(const NR& r);
int32_t bump_nr(NR& r);
int32_t push_list(std::vector<int32_t>& xs, int32_t v);
template <class T>
int32_t point_v(T p) { return p.v; }
}
