// Fastify-style verb call with an inline arrow handler (no named handler → "—").
import Fastify from "fastify";

const fastify = Fastify();

fastify.get("/health", async (request, reply) => {
  return { ok: true };
});

export default fastify;
