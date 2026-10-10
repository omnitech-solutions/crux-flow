# Consumer fixtures for technology guidance

Small copies of the two repositories that use Crux Flow's technology guidance, built from committed
content only (`git show HEAD:<path>`), never from a working tree:

| Fixture | Repository | Commit |
|---|---|---|
| `studio/` | omnitech-interview-answers-generator (Interview Studio) | `0ad961d2d2944b32218d176c77c26f7e955a3d17` |
| `engine/` | omnitech-ai-engine | `3e933f9a8f490c1963a3cb590ea792b853bd5eb1` |

What is real: every manifest's name, scripts and dependency sections (internal workspace packages
removed), the pnpm catalog, the Swift tools line, the PostgreSQL image tag, the root `AGENTS.md`
rules, the Studio's hand-written router and reference map, its nine research source pages and their
`PROVENANCE.md` notes, and the opening lines of each decision and invariant a layer cites.

What is a stand-in: source files (one comment line each, so a layer's path exists), the compose
files (one service), and the bodies of the other Studio skills. No upstream rule text, no
application source and no environment file is copied.

`<name>.crux-flow.yml` is the project configuration each test installs, and the one prepared for the
real repository. `<name>.triggers.json` holds the router's trigger evaluations as data: prompts that
should load it (with the layer they belong to) and prompts that should not.

The Studio links `.claude/skills` and `.opencode/skills` to `.agents/skills`. A test makes those
links when it copies the fixture; none is stored here.
