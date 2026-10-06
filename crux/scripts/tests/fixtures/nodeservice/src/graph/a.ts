// Module-graph fixture: a resolving relative import, a dropped bare package, a
// tsconfig `@/*` alias import, a commented import, and a string-literal import.
import { b } from "./b";
import express from "express";
import { c } from "@/graph/c";
// import "./d";
const label = "import y from './b'";
export const a = 1 + express.length + b + c + label.length;
