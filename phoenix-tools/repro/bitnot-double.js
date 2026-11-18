function bitNot(x) {
  return ~x;
}
let n = 0;
for (let i = 0; i < 100; i++) {
  n += bitNot(i + 0.5);
}
print(n, bitNot(1e20));
