# Strategic Recommendations — PatchForge

> Personal reference doc. Written 2026-07-19 after closing out issue #258.
> Opinions, not decisions. Ordered by expected value, not by ease.
> Read when planning what to do next, especially when tempted by a big feature.

---

## The one paragraph you should re-read every month

PatchForge's edge is **determinism as a product feature**, not as an
implementation detail. Every design decision that trades determinism for
speed, autonomy, or convenience makes the product worse — even if it makes
the demo better. The market will eventually reward this, but the window is
12–18 months before Anthropic/OpenAI ship something with "safe apply"
guarantees. Use that window to become irreplaceable for a specific
audience, not to become "better than Cursor for everyone".

---

## Top 5 recommendations, ranked by value

### 1. Ship a validator plugin system before anything else in P5

**Why it matters most:** every single non-you user hits this on day one.
`ruff` + `pytest` hardcoded in `validator/runners.py` is the single
biggest reason a curious visitor tries PatchForge on their repo, fails,
and never comes back. All other technical debt is invisible to new users;
this one is fatal on first contact.

**Shape:** a `validators` field in `orchestrator.json` that names
adapters (`ruff`, `pytest`, `flake8`, `mypy`, `pylint`, `unittest`,
`tox`, or a shell command with a stable exit-code contract). Adapters
live in `src/orchestrator/agents/validator/adapters/`, one file each,
implementing a small typed interface (`run() -> ValidatorOutput`).
Existing `ruff`/`pytest` become the first two adapters, not special
cases.

**Anti-pattern to avoid:** don't build a "plugin discovery" system with
entry points, dynamic loading, or a marketplace. Ship 4-5 adapters
in-tree, document how to add one, and let users PR the rest. YAGNI on
the meta-system.

**Estimated size:** medium (~2-3 weeks solo). Well-scoped, splittable.

### 2. Pick one vertical and get one real user in it, before more features

**Why:** you're currently the only user. Every design decision is
implicitly optimized for your repo, your team size (1), your workflow.
This is dogfooding trap: you'll build features that solve your problems
and miss the ones that matter to the market you actually want.

**Recommended vertical:** fintech or regulated healthcare, not "enterprise"
broadly. Two reasons: (a) the audit bundle + GPG signing work you already
shipped is only valuable to industries that legally need it, so you're
already halfway there; (b) these industries pay for compliance tools in
ways general dev-tools users don't.

**Concrete action:** find one small-to-medium fintech or health-tech
company (10-50 devs) where a friend/contact has an in. Offer them free
usage in exchange for weekly feedback for 3 months. Their problems become
your P5 priorities. If you can't find one, that itself is important
signal about product-market fit.

**Anti-pattern to avoid:** "let's launch on Hacker News and see who
shows up." HN gives you 500 tire-kickers and zero paying users. One
real user in a real vertical is worth 1000 GitHub stars.

### 3. Simplify apply.py before it collapses under its own weight

**Why:** `apply.py` is at ~1000 lines and growing after #258's four parts.
It now carries lifecycle detection, WAL hydration, resume execution,
dirt capture/restore across four sub-cases, isolation checks, lock
management, config snapshotting, and rollback. This is close to the
complexity ceiling one person can sustainably modify without introducing
bugs. The empirical evidence: the original single-PR draft of #258
grew this file to ~1000 lines and hid several bugs until after-the-fact
review — you already lived this.

**Shape:** extract at minimum three modules:
- `apply/lifecycle_gate.py` — the pre-execution classification +
  dispatch (CONFLICT/STALE/REBASEABLE/VALID/ALREADY_APPLIED branch).
- `apply/dirt_manager.py` — the `--allow-dirty` capture/restore lifecycle,
  including sub-cases 0/1/2 from Part 4.
- `apply/resume.py` — WAL hydration + triple isolation check.

`apply.py` itself becomes a thin orchestrator (~200 lines) that reads
like the pipeline it is.

**Anti-pattern to avoid:** don't refactor before writing more tests
against the current behavior. The AC-challenge round on #258 already
flagged that `classify_lifecycle` is only tested via mocks — that gap
must close *before* you refactor, or you'll break wire-ups nothing
catches.

**Estimated size:** medium, but risky. Do it in 2-3 sequential PRs, not
one bundled refactor.

### 4. Kill scope creep in P5. Cut ruthlessly.

**Current roadmap has (from CONTEXT.md):** Core P4–P5, Scout as separate
product line, auto-seeding tests, multi-language, SaaS control plane,
observability integrations, etc. For one dev, this is at least 3 years
of work. In 3 years the market will be different — either you'll have
lost the window, or you'll have burned out trying to hit all of it.

**Concrete cuts (my ranking, most-to-least deferrable):**
- **Defer to P6+:** multi-language support. This is a 10x scope increase
  that isn't unlocked until you've won a niche in Python first.
- **Defer to P6+:** SaaS control plane. This is the *monetization* layer,
  not the product. Build it after you have paying users asking for it.
- **Defer indefinitely:** auto-seeding characterization tests. This is
  an open research problem (Pynguin/Diffblue tried; neither made it).
  Don't put your reputation on a hard AI problem you can't guarantee.
- **Keep in P5:** validator plugins (see #1), configurable timeouts,
  one polished CI integration (probably GitHub Actions).
- **Keep, but ship as a Scout follow-on, not core:** issue-registry
  polish, characterization-tests-adjacent features.
- **Keep for P6, not P5:** PR comments on GitHub Actions (audit summary +
  risk level posted directly on the PR). High value, but requires a GitHub
  App (not just an Action), webhook handling, and a persistent listener —
  easily 2-3 months. Only build after at least one real user asks for it
  from a CI/CD workflow. The `/patchforge approve` trigger in comments is
  the differentiating part; the report alone is table stakes.

**The test:** if a P5 item doesn't directly serve the vertical you
picked in recommendation #2, it's out.

### 5. Fix the timeout/config hardcoding while it's still trivial

**Why:** 30-second git timeouts in `git.py` will fail silently on any
large repo, any slow filesystem (NFS, remote Docker volumes, WSL2 on
mechanical drives). Same for patch-size limits, validator timeouts, etc.
This is a category of "obvious bugs waiting to be reported" that you can
close preemptively in ~1 week total by centralizing them in
`orchestrator.json`.

**Shape:** one `TargetConfig.timeouts` sub-model with named entries
(`git_op`, `validator_run`, `patch_apply`), defaults preserving current
behavior, all `subprocess.run(..., timeout=30)` sites read from it.

**Why now not later:** the longer these constants live in code, the
more places they get copied to. Fix once, cheaply, before the surface
grows.

**Natural extension:** once `validators` is a first-class config key
(rec #1), add `anchor_tests` as an optional list under it — e.g.
`orchestrator.json: { "validators": { "pytest": { "anchor_tests":
["tests/test_auth.py"] } } }`. The contract: anchor suite runs first;
if it fails, abort early without running the full suite. This is
"test anchoring" without touching `IssueContract` schema, avoiding
the schema_version debt. Scope: ~1 day once validator plugins land.

---

## Traps to avoid (things that look like good ideas but aren't)

- **Adding parallelism to the pipeline.** This is the analysis's #1
  criticism, and it is exactly wrong. Determinism is the product.
  Parallelism voids the rollback guarantees that make PatchForge
  different from Cursor. Refuse this recommendation from every future
  reviewer — it means they don't understand the product.

- **A "plugin marketplace" or extension registry.** You're one person.
  Every mechanism you build to let others extend the product is a
  mechanism you have to maintain. Ship in-tree adapters, take PRs, and
  say no to the marketplace idea for 2 more years.

- **Framework rewrite (async, event-driven, etc.).** The synchronous
  monohilo pipeline is a *feature*, not a limitation. Don't let someone
  convince you to rewrite it in `asyncio` for "scalability" you don't
  need.

- **Chasing "AI autonomy" trends.** Every 6 months there will be a new
  agent framework everyone raves about. Your differentiator is that
  LLMs *don't* control the pipeline — they interpret and propose. Every
  time you're tempted to add autonomous behavior, ask: does this
  preserve the "human approves diff before apply" invariant? If not,
  don't build it.

- **Trying to "win Hacker News".** HN is a demo audience, not a
  customer audience. Your customers read industry publications, attend
  compliance conferences, and hire based on RFP responses. Marketing to
  HN gets you tire-kickers; marketing to CTOs at 50-person fintechs
  gets you revenue.

- **Believing your own PR/analysis writeups.** The critique-and-vision
  document from earlier today read impressively but was ~40% wrong on
  concrete facts (GPG mocking claim was false; the SQLite point cited a
  fix as a criticism; determinism was misidentified as a limitation).
  When you get glowing external assessments, discount them heavily.
  When you get harsh ones, extract the 20% that's true.

---

## Sustainability warnings (specific to solo-dev risk)

- **The 4-role planning process is your moat, but also your bottleneck.**
  Every issue costs you ~4-8 hours of process (clarify → challenge →
  plan → adversarial → implement → diff-review → QA). You cannot
  personally sustain 100 issues/year at that rate. Two options: hire
  help, or reduce the ceremony for lower-risk changes. The current
  discipline is right for issues like #258 (data integrity, rollback);
  it's overkill for adding a new adapter or fixing a typo. Consider a
  tiered process.

- **You are single-point-of-failure for the project.** If you take a
  month off, everything stops. Before P5 finishes, either (a) recruit
  one collaborator who understands the tesis and can review PRs
  independently, or (b) write down enough about *why* decisions were
  made (not just *what* was decided) that someone could resume the
  project cold. `docs/context/` is a good start; the plan docs are
  even better; but there's no doc that captures the *reasoning style*
  itself. Consider writing one.

- **Your dogfooding is your quality gate.** The moment you stop using
  PatchForge to develop PatchForge, subtle regressions will land. This
  is a strength — protect it. Any decision that would make you *not*
  want to use PatchForge on itself is a design smell.

---

## What I'd do if I were you, in order, over the next 6 months

1. **Month 1:** ship the validator plugin system (rec #1). Small,
   well-scoped, unlocks all future users.
2. **Month 1-2:** ship configurable timeouts (rec #5). Cheap insurance.
3. **Month 2:** find one real user in the chosen vertical (rec #2).
   This shapes months 3-6.
4. **Month 3:** refactor `apply.py` into 3 modules (rec #3). Now that
   you have real usage feedback informing what to protect.
5. **Month 4-6:** whatever the one real user needs. Not what your
   roadmap says. Not what HN says. Not what an AI analysis document
   says. What the person actually using your product for real work
   says.

If at month 3 you don't have a real user, stop building features and
spend the next two months on user acquisition, not code. A tool with
no users has no product-market fit no matter how well-engineered.

---

## Final honest line

PatchForge is the best-engineered solo-dev project I've seen in this
space. The tesis is correct, the discipline is real, the code shows
craft. The risk isn't quality — it's that you'll build a masterpiece
nobody uses because you optimized the engineering and postponed the
market work. Fix that imbalance and this becomes something serious.
