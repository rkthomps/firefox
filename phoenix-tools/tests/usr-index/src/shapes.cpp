#include "shapes.h"

namespace geo {

Shape::Shape() {}

int Shape::size() const { return 1; }

int Shape::size() { return 2; }

template <>
int twice<int>(int v) {
  return 2 * v;
}

int scale(int v) { return v * 2; }

double scale(double v) { return v * 2; }

void usePair() { Pair pair; }

}  // namespace geo
