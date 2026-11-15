// Shared test helpers: fixture loading and a fixed time zone so HH:MM assertions are stable.
process.env.TZ = "Asia/Kolkata";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
export const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../web");
export const fixture = (name) => JSON.parse(fs.readFileSync(path.join(WEB, "fixtures", name), "utf8"));
export const s = (x) => String(x);
export const count = (hay, needle) => hay.split(needle).length - 1;
