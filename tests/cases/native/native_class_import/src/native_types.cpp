#include "native_types.hpp"

int32_t ns::Vec2::sum() const {
    return x + y;
}

int32_t Rect::area() const {
    return w * h;
}

int32_t vec2_sum(ns::Vec2* v) {
    return v->x + v->y;
}

extern "C" int32_t rect_area(Rect* r) {
    return r->w * r->h;
}
