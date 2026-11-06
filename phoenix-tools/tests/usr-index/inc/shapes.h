// The fixture of phoenix-tools/tests/test-usr-index.py.
#ifndef SHAPES_H
#define SHAPES_H

#define DECLARE_AREA(name) double name##Area() const;

namespace geo {

enum Color { Red, Green };
enum class Kind { Circle, Square };

struct Point {
  int x;
  int y;
};

class Shape {
 public:
  Shape();
  DECLARE_AREA(circle)
  int size() const;
  int size();
  bool operator()(int n) const;
};

template <typename T>
T twice(T v) {
  return v + v;
}

template <>
int twice<int>(int v);

template <typename T>
struct Box {
  T value;
};

template <>
struct Box<int> {
  int count;
};

template <typename T>
T half(T v);

typedef long long Count;

template <>
Count half<Count>(Count v);

template <>
double half(double v);

#define DECLARE_HALF(type) template <> type half<type>(type v);
DECLARE_HALF(float)

struct Holder {
  template <typename T>
  bool is() const;
};

template <>
bool Holder::is<Shape>() const;

template <typename T>
struct Wrapper {
  operator geo::Box<T>() const;
};

int scale(int v);
double scale(double v);

namespace {
int hidden = 0;
}

}  // namespace geo

#endif
