# PatchForge Agent Contract

Read this file before working in the repository. It defines the session
contract for every AI assistant.

## Session lifecycle

1. Read `AGENTS.md`.
2. Read `docs/context/CONTEXT.md`.
3. Read `docs/context/Workflow.md`.
4. Read task-specific documentation and relevant ADRs.
5. Inspect impacted code.
6. Inspect affected tests.
7. Produce an implementation plan.
8. Wait for approval.
9. Implement the approved scope.
10. Validate the result using the required QA gates.

## Working rules

- Do not invent requirements or broaden the approved scope.
- Implement the smallest correct change that satisfies the acceptance criteria.
- Preserve public APIs, established architecture, and documented invariants.
- Reuse existing patterns before introducing new ones.
- Keep diffs focused; do not fix unrelated technical debt.
- Record newly discovered out-of-scope debt in `docs/context/discoveries.md`.
- Write code, comments, commits, and pull requests in English.
- Do not add AI attribution or `Co-Authored-By` trailers.

`docs/context/Workflow.md` is the canonical development process. ADRs are the
canonical rationale for architectural decisions. `docs/context/CONTEXT.md` is
the canonical current project state.
