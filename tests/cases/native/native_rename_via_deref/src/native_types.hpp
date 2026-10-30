#pragma once
#include <cstdint>

namespace x {

struct Widget {
    int32_t id_;
    int32_t getId() const { return id_; }
    template <class T>
    int32_t echo(const T&) const { return 7; }
};

}  // namespace x
