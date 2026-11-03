// One `~` site: attach the int32 stub, run it on edge values, then send it
// values that fail its guard. Run with --no-ion --baseline-eager.

function bitnot(x) {
  return ~x;
}

const int32s = [0, 1, -1, 5, 0x7fffffff, -0x80000000];
for (let i = 0; i < 50; i++) {
  for (const x of int32s) {
    assertEq(bitnot(x), (-x - 1) | 0);
  }
}

assertEq(bitnot(1.5), -2);
assertEq(bitnot(2 ** 32 + 5), -6);
assertEq(bitnot(true), -2);
assertEq(bitnot(null), -1);
assertEq(bitnot(undefined), -1);
assertEq(bitnot("7"), -8);
assertEq(bitnot(10n), -11n);

for (const x of int32s) {
  assertEq(bitnot(x), (-x - 1) | 0);
}
