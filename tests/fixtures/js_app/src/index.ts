import React from "react";
import { pick } from "lodash/fp";
import * as util from "./util";
import type { Shape } from "./types";
import "./missing";
export * from "./util";
const lazy = import("./lazy");
const tpl = import(`./lazy`);
const dyn = import(pickName());
const dyn2 = import(`./plugins/${pickName()}`);
// import hidden from "./nope";
/* import hidden2 from "./nope"; */
const text = "import str from './nope'";
const tpl2 = `import tpl from './nope'`;
const re = /import re from "\.\/nope"/;
