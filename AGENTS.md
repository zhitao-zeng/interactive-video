# Clean Studio

- This repository is independent. Historical projects and their worktrees are read-only evidence and provenance sources; never import their runtime code or modify their files.
- The first production target is W10 with H3 first frames, 1080x1920 at 25 fps, H.264/AAC MP4, JPEG cover/end screens, and a host-owned three-second end card.
- Keep original inputs and generated media out of Git. Immutable snapshots live under `inputs/`; their origin path, SHA-256, and snapshot date must be recorded. Never overwrite a snapshot to hide an input change.
- Reference manifests verify file existence and SHA-256 at build time. Rechecking changes after manifest construction belongs to Task 4's single pre-payment gate; Task 1 does not duplicate that runtime check.
- Author shots contain design facts only. Host-owned opening, visual, motion, and storyboard derivations must not become author fields. Preserve every source segment, speaker, line, and fixed end-card target.
- Task 1 end-state screening is limited to finite pure-production/non-state atoms and their full-string combinations. All remaining semantic judgment belongs to Task 3; do not expand Task 1 into a semantic reviewer.
- Domain values are immutable. Mutable production state belongs in the later SQLite store, not in source parsing or reference manifests.
- Do not store secrets, call real models, generate production media, or upload during offline implementation and tests.
- Run tests with the existing interpreter only; do not install into it:

  ```bash
  PYTHONPATH=src:tests NOVEL_PROJECT_ROOT="$PWD" \
    /mnt/disk1/zengzhitao/interactive-video-worktrees/main/.venv/bin/python \
    -m pytest -q
  ```
