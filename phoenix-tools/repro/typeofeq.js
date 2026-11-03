// Attaches a TypeOfEq stub for an object, whose CacheIR
// (LoadTypeOfEqObjectResult) has a TypeofEqOperand argument.
function isFunction(x) {
  return typeof x === "function";
}
let n = 0;
for (let i = 0; i < 100; i++) {
  n += isFunction({}) ? 1 : 0;
}
print(n);
