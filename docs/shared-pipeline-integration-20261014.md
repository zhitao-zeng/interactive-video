# Shared pipeline integration — 2026-10-14

User authorization: consolidate common code into codex/feat/shared-pipeline, test and merge into main,
then synchronize those fixes to all three content branches while preserving their content work.

## Preserved snapshots

- base: `codex/adventure-demo` at `fcd6787589`.
- romance: `codex/romance-sd25-continuity` at `e3de984031`.
- adventure: `codex/adventure-rhythm-20261013` at `74041c39d4`.
- suspense: `codex/suspense-reference-study-20261013` at `61c8fa6514`.

## Integrated implementation

- Romance: continuous multi-turn SD/H3 compilation, state and luggage continuity fields, protected SD conversation lines, primary frame evidence and original/editorial source documents.
- Adventure: authored intake, source-preserving packing, reference-view correction, independent picture observation, actual shot-span evidence timing and native sampling report.
- Suspense: scoped identity/portrait review, evidence panels and interaction ambience, Agent tool/context/runtime corrections, strict request consistency checks.
- Common production: one project-aware CLI and H3/SD repair loop, preserved task/take budgets and exact 75-frame tail. Configurable style kinds avoid reintroducing animation instructions in live-action repairs.

The integration commit is 81b379e. Full local regression: **3263 passed**, exit 0.
Independent code review found no blocking issue. CLI help confirms all three projects and both provider lanes.
No video/model call, remote service change or push was made for this integration.

## Content boundaries

Actual scripts under projects/, media/assets/input packages and generated runtime/delivery records stay in
the owning branches/worktrees. Five exact-story/package regression modules stay with their content;
the shared intake test uses a small self-contained material fixture.

Each branch keeps its own configs/interactive.json runtime choices. Agent implementation/runtime fixes
are shared. No romance single-episode silence waiver is generalized to main.

Adventure acquired new sound/native-text work after its snapshot; it is retained as branch-local delta
when synchronizing main, rather than silently imported into this validated main release.

## Reconciliation decisions

Keep multi-turn guidance over the older single-utterance skill wording; retain both portrait-scope and
viewer-first checks. Merge panel placement with observed shot spans, retaining both source branches'
regression coverage. Fix the generic compiler target rather than letting a repair reintroduce a cartoon
instruction. These decisions do not change authored episode facts or existing output histories.

## Completed synchronization

All three content branches merge the validated main release. Shared source is identical on romance and suspense; adventure retains its separately captured sound/native-text/reference-asset additions on top.

- main: **3263 tests passed**, exit 0.
- romance: **3268 tests passed**, exit 0.
- adventure: **3287 tests passed**, exit 0.
- suspense: **3276 tests passed**, exit 0.

Original content runtime configurations and local input/media symlinks remain intact. Original studio worktree remains on its snapshot branch; use the dedicated main worktree for the stable implementation. All commits and merges are local; no push or model/service operation was performed.
