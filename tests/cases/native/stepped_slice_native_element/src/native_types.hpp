#pragma once
#include <cstdint>

// No default constructor: list code must not ask for one.
struct NativeItem {
    int32_t n;
    explicit NativeItem(int32_t n) : n(n) {}
};
