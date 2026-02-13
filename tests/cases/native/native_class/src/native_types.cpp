#include "native_types.hpp"

int32_t Vec2::sum() const {
    return x + y;
}

int32_t Vec2::dot(const Vec2& other) const {
    return x * other.x + y * other.y;
}

Vec2 Vec2::zero() {
    return Vec2{0, 0};
}

int32_t ns::Color::brightness() const {
    return (r + g + b) / 3;
}

int32_t vec2_sum(Vec2* v) {
    return v->x + v->y;
}

int32_t color_brightness(ns::Color* c) {
    return (c->r + c->g + c->b) / 3;
}
