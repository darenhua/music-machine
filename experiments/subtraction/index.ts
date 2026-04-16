#!/usr/bin/env bun
const args = Bun.argv.slice(2);

if (args.length < 2) {
  console.error("Usage: subtraction <a> <b> [<c> ...]");
  process.exit(1);
}

const numbers = args.map((arg) => {
  const n = Number(arg);
  if (Number.isNaN(n)) {
    console.error(`Invalid number: ${arg}`);
    process.exit(1);
  }
  return n;
});

const result = numbers.slice(1).reduce((acc, n) => acc - n, numbers[0]!);
console.log(result);
