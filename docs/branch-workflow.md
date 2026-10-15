# Shared pipeline and content branches

The user approved local integration and synchronization to all three content branches on 2026-10-14.

| Owner | Branch | Files |
|---|---|---|
| Stable shared pipeline | main | src/, common tests, provider and Agent configuration, reusable CLI and adapters |
| Integration | codex/feat/shared-pipeline | Changes intended for all content branches; test before merging |
| Romance | codex/romance-sd25-continuity | projects/romance, romance experiments and content-specific regressions |
| Adventure | codex/adventure-rhythm-20261013 | projects/adventure, adventure/travel experiments and regressions |
| Suspense | codex/suspense-reference-study-20261013 | projects/suspense, research and suspense content regressions |

All three use the same versioned implementation. Topic adapters are common code, not independent copies
of the whole pipeline. Explicit `--project` and `--material` choose the content. Outputs and generation
histories stay in the original owning worktrees.

The common repair principles in `skills/episode-production/SKILL.md`, the shared
`references/case-lessons.md`, and the common principles/stability examples in
`skills/video-repair/SKILL.md` are shared guidance. Publish their changes through
`shared-pipeline` and `main`, then synchronize the same guidance into all three
content branches and the independent novel repository. Preserve local tool
contracts and in-progress planning/camera guidance; they follow the code that
actually implements them. Genre creation skills remain separate.

Check the installed Skill as well as the repository source when resuming an
Agent. A code checkout, an installed workspace copy and an earlier conversation
are different states. Preserve old attempt snapshots and running jobs; refreshed
guidance must be read on the next authorized invocation before claiming it was
used. Synchronizing a Skill does not authorize generation or renew a budget.

## Routine work

1. Work in the existing topic worktree and keep its branch. Commit topic material independently.
2. For a common fix, bring the minimal source/test change to the integration branch, run applicable tests,
   and merge tested work into main. Do not copy whole worktrees or runtime reports.
3. Merge main into each topic branch. Preserve that branch's `projects/`, authored experiments,
   content-only tests and `configs/interactive.json` runtime choices. Reconcile genuine source conflicts
   rather than accepting all of one side.
4. Never merge over uncommitted work from another conversation. Capture an agreed snapshot or retain
   the newly edited files and complete synchronization when their owner has settled them.

`configs/interactive.json` is deliberately branch-owned: resolution, service selection and local asset
publication paths can differ. Shared main ships a portable default. `configs/agent_sandbox.json` and
Agent implementation carry the validated common context window, bounded tools and repair protocol.
Episode QC exceptions live with that episode; none are inherited from an exported film into main.

The old `codex/adventure-demo` worktree is retained as the original studio snapshot, not a fourth production
content branch. Main has its own worktree so no active conversation is switched underneath its work.

No remote repository or push is needed for this local workflow. Synchronization is a Git merge with
preserved history, not a reset or destructive folder copy.
