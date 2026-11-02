#pragma once
#include <cstdint>

struct NBox {
    int32_t v_ = 0;
    int32_t get_v() const { return v_; }
    void set_v(int32_t x) { v_ = x * 10; }
};

inline NBox g_box;
inline NBox* open_box(int32_t) { return &g_box; }
inline NBox make_box() { return NBox{}; }
