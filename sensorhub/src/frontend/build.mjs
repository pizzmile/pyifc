import { cp, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(root, "dist");

await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, "vendor"), { recursive: true });
await cp(path.join(root, "src"), output, { recursive: true });
await cp(
  path.join(root, "node_modules/angular/angular.min.js"),
  path.join(output, "vendor/angular.min.js"),
);
await cp(
  path.join(root, "node_modules/chart.js/dist/chart.umd.js"),
  path.join(output, "vendor/chart.umd.js"),
);