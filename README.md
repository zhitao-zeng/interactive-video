# Interactive video studio

One shared H3 / SD2.5 production pipeline for adventure, romance and suspense material.

- `main`: validated common implementation and adapters.
- `codex/feat/shared-pipeline`: integration and regression testing.
- Content branches: approved scripts, imported references, experiments and runtime choices.

Read [branch workflow](docs/branch-workflow.md) and [common material intake](docs/interactive-common-intake.md).

## Use a prepared material directory

```bash
python scripts/interactive_video.py prepare --project romance --material /path/to/approved-material --edition romance-demo
python scripts/interactive_video.py assets --project romance --edition romance-demo --kind all
python scripts/interactive_video.py run --project romance --edition romance-demo --lane h3
python scripts/interactive_video.py run --project romance --edition romance-demo --lane h3 --resume
```

`adventure` and `suspense` use the same commands; `sd25` selects the other video provider.
Preparing preserves source documents, ordered shots, voice ownership and interaction choices.
Asset and run commands call configured model services. `--resume` preserves task receipts and take budgets;
it does not grant new retries or certify a candidate film. Review, repair and media acceptance remain separate.

Use a project environment matching `pyproject.toml` / `uv.lock`; credentials remain in the local `.env`.
Run regression tests with `PYTHONPATH=src python -m pytest`. Tests on main use synthetic/self-contained
fixtures. Real-package and exact-story regressions remain on their content branches.
Generated videos, models, input packages, `.runtime` and delivery files are not tracked by Git.
The upstream novel tools remain for compatibility; do not point them at another project's production data.
