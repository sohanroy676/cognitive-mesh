import fs from "node:fs";

export const read = (p: string): Buffer => fs.readFileSync(p);
