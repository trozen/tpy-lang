#include "native_types.hpp"

int32_t vec2_sum(Vec2* v) {
    return v->x + v->y;
}

int32_t color_brightness(ns::Color* c) {
    return (c->r + c->g + c->b) / 3;
}
