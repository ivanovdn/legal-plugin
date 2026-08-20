// Minimal ambient declarations for the two Node APIs the eval runner needs.
//
// tsconfig pins "types": ["office-js", "vite/client"]. Pulling in @types/node
// would change typing across every browser and Office.js source in this package
// for no benefit, so — exactly as testAssert.ts does with `declare const
// process` — we declare only what is used.
declare module "node:fs" {
  export function readdirSync(path: string): string[];
  export function readFileSync(path: string, encoding: "utf8"): string;
}

declare module "node:path" {
  export function join(...parts: string[]): string;
  export function resolve(...parts: string[]): string;
}
