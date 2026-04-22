#pragma once
#include <cstdint>

namespace x {

struct Widget {
    int32_t id_;
    int32_t getId() const { return id_; }
};

}  // namespace x
