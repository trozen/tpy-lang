#pragma once
#include <cstdint>

namespace mylog {
struct NR {
    int32_t x;
    explicit NR(int32_t x);
};

int32_t use_nr(const NR& r);
}
