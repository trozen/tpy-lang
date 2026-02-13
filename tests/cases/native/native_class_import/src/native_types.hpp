#pragma once
#include <cstdint>

namespace ns {
struct Vec2 {
    int32_t x;
    int32_t y;
    int32_t sum() const;
};
}

struct Rect {
    int32_t x;
    int32_t y;
    int32_t w;
    int32_t h;
    int32_t area() const;
};

int32_t vec2_sum(ns::Vec2* v);

#ifdef __cplusplus
extern "C" {
#endif

int32_t rect_area(Rect* r);

#ifdef __cplusplus
}
#endif
