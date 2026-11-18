MCP Security Review Follow-up — Close Remaining Git Confirmation Gaps

Work ONLY in mcp-server/. Do not change S7 or root app files. Do not commit or push.

A code review of the first hardening pass found that git_commit still uses the generic confirmation COMMIT VERIFIED CHANGES, which is not bound to the staged diff. git_push checks branch and HEAD SHA but does not include the upstream destination in the confirmation. The preflight subprocesses also use unbounded capture_output for some Git metadata.

Required fixes:
1. Add git_commit_preview MCP tool. Validate staged-only state, reject unstaged and untracked files, reject sensitive/excluded staged paths, require at least one staged change, and return branch, HEAD, stable digest of staged tree (prefer git write-tree), plus exact confirmation bound to branch + staged tree digest.
2. git_commit must recompute all preconditions and staged tree digest immediately before committing; require exact confirmation matching current branch and staged tree digest. Never stage automatically.
3. Add git_push_preview MCP tool. Require clean working tree, non-detached branch, configured upstream, valid HEAD SHA. Return branch, HEAD SHA, upstream remote/branch, and exact confirmation bound to all three.
4. git_push must recompute branch, SHA, upstream, clean-tree state immediately before pushing; require exact phrase including branch + full SHA + upstream. Push explicit remote and HEAD:upstream-branch, never force.
5. Bound internal Git metadata output. Do not use unbounded capture_output for potentially large status/path lists. Reuse bounded process handling or implement a streaming bounded helper. Reject truncated output. Limit staged paths to 500.
6. Add tests for successful preview/commit flow; changed staged tree invalidates old confirmation; push preview includes upstream; wrong upstream confirmation rejected; successful push targets exact upstream; oversized Git metadata rejected. Use temporary local bare remote for push tests. No network.
7. Fix README wording and accurately describe preview/confirmation workflow. Update protocol smoke expected tools to include preview tools.
8. Keep hooks enabled and document hook execution risk. No force push. No commits or pushes to the real repository.

Verification: run all MCP unittests, compileall, protocol smoke test, root git diff --check. Report actual test count and limitations.