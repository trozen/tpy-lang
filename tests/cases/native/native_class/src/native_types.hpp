#pragma once
#include <cstdint>

struct Vec2 {
    int32_t x;
    int32_t y;
    int32_t sum() const;
    int32_t dot(const Vec2& other) const;
    static Vec2 zero();
};

namespace ns {
struct Color {
    int32_t r;
    int32_t g;
    int32_t b;
    int32_t brightness() const;
};
}

int32_t vec2_sum(Vec2* v);
int32_t color_brightness(ns::Color* c);
