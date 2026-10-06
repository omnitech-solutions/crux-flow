// Express-style route registrations with a named handler on each verb call.
const express = require("express");
const app = express();
const router = express.Router();

// A decoy Map lookup — Map.get("x") must NOT be read as a route (first arg
// is a string literal that does not start with "/").
const registry = new Map();
registry.get("x");

// A decoy cache read — cache.get(key) must NOT be a route (first arg is not
// a string literal at all).
const cache = { get: (k) => k };
cache.get(key);

// A commented-out route — the comment strip must drop it.
// app.get("/ghost", ghostHandler);

app.get("/users", listUsers);
router.post("/users/:id", updateUser);

module.exports = { app, router };
