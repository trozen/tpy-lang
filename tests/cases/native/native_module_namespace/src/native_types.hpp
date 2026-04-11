#pragma once
#include <cstdint>

namespace mypkg {
struct Vec2 {
    int32_t x;
    int32_t y;
};

inline Vec2 add_vecs(Vec2& a, Vec2& b) {
    return Vec2{a.x + b.x, a.y + b.y};
}
}

namespace other {
struct Thing {};
}
