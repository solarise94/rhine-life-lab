import { existsSync, lstatSync, mkdirSync, rmSync, symlinkSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(root, ".next/static");
const target = resolve(root, ".next/standalone/frontend/.next/static");

if (!existsSync(source) || !existsSync(dirname(target))) {
  process.exit(0);
}

mkdirSync(dirname(target), { recursive: true });

if (existsSync(target) || lstatSync(target, { throwIfNoEntry: false })) {
  rmSync(target, { recursive: true, force: true });
}

symlinkSync(source, target, "dir");
console.log(`Linked standalone static assets: ${target} -> ${source}`);
