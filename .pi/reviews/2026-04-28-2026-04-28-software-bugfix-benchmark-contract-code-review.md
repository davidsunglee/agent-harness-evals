# Code Review — Software Bugfix Benchmark Contract

## Review Pass 1 — Full Review

### Strengths
- `shared/task-spec.md:1-148` replaces the placeholder with a concrete v1 contract that matches the requested structure and keeps the spec framework-agnostic.
- `shared/task-spec.md:17-32` defines the reusable case format, including edit constraints and default anti-gaming rules, while preserving room for additional cases later.
- `shared/task-spec.md:34-105` clearly separates agent input, expected behavior, required capabilities, and output schema, and it correctly keeps task-specific fields nested under the shared `input`/`output` envelope defined in `shared/contract.md`.
- `shared/task-spec.md:107-138` makes harness-derived observations authoritative, documents independent scoring categories, and explicitly avoids a weighted or overall v1 leaderboard.
- Repo-wide documentation remains consistent: `README.md`, `shared/contract.md`, `evals/README.md`, `frameworks/README.md`, and all eight framework READMEs still point at the shared spec without stale placeholder wording.

### Issues

#### Critical (Must Fix)
- None.

#### Important (Should Fix)
- None.

#### Minor (Nice to Have)
- None.

### Recommendations
- None.

### Assessment

**Ready to merge: Yes**

**Reasoning:** The documentation change fully replaces the placeholder with the required benchmark contract, aligns with the shared transport envelope, and leaves the surrounding repo documentation semantically consistent.

---

## Final Verification — Full Diff Review

### Strengths
- Full-diff verification against the implementation range confirms the only behavioral change is the intended rewrite of `shared/task-spec.md`.
- The final document satisfies the required acceptance points: mutable per-run worktree input, in-place editing model, harness-derived canonical diff and test rerun, non-authoritative agent self-reporting, framework-native capability allowance, and independent scoring categories with no aggregate.

### Issues

#### Critical (Must Fix)
- None.

#### Important (Should Fix)
- None.

#### Minor (Nice to Have)
- None.

### Recommendations
- None.

### Assessment

**Ready to merge: Yes**

**Reasoning:** Final verification found no Critical or Important issues in the full diff. The spec is production-ready as a documentation contract for v1.

---

## Remediation Log
- No remediation batches were required.
- Verification checks run:
  - `grep -nR "task-spec" --include='*.md' README.md shared evals frameworks`
  - `grep -nR "TODO — fill in\|single non-trivial use case" --include='*.md' README.md shared evals frameworks`
  - `grep -n "task-spec" shared/contract.md`
  - `grep -in "independent\|reaches in only through the contract" frameworks/README.md`
  - `grep -in "scores them against\|comparative report\|across frameworks" README.md evals/README.md`
  - `grep -nR "../../shared/task-spec.md" frameworks/ --include='README.md'`
- **Result:** Clean after 1 iteration.
