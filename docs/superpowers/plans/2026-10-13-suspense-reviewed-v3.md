# Suspense reviewed v3 implementation plan

> Execute inline with executing-plans. User explicitly authorized continuation and enabled review; keep old editions and do not commit or push.

Goal: replace the demonstrated spatial/prop failures with fixed shot references and a reviewed H3 edition.
Architecture: keep the existing H3 compiler, media runner and bounded review/repair loop. Add only an explicit evidence-panel placement to existing delivery overlays. Prepare a new independent edition with authored single-action shots and fixed framing references.
Spec: .runtime/suspense-h3-clarity-review/diagnosis.json and the user's instruction to continue with review enabled.

## Constraints
- Native H3 768P, portrait export 1080x1920/25fps/H.264/AAC; exactly 3 seconds of interaction.
- All conversational actors already stationary; no conversational entrance. Only S06 enters801, only S10 exits; doors fully open and stationary.
- Tenant remains at802 doorway on corridor tiles; investigator's questions happen from the shared corridor. No cross-room dialogue.
- Phone shot contains no hands. Records remain below portrait crop; reliable large evidence text added by delivery. No017 reveal.
- Enable verify review and whole-take cast video; calibrate against known bad old clip before full production. Existing bounded repair only; no manual intervention during generation.

## Review focus
Known repeated entrances, changed room geography, accidental paper typography, extra offscreen-speaker actor, and review capability on a confirmed old failure.

## Tasks
1. Preserve optional placement metadata in evidence_events; select a larger EvidencePanel ASS style below the face and above dialogue. Tests in tests/test_evidence_panels.py must fail before the change and pass after it; existing overlay timing remains unchanged.
2. Prepare v3 from clarity-v2, preserving all dialogue and source IDs. New single framing references, short fixed-camera entry/exit, phone-only shot, narrator choices on investigator. Write authored_groups for existing automated request repair.
3. Independently review the small code change and prepared inputs, run relevant tests, then run the full reviewed pipeline against the confirmed working local Qwen endpoint. Evaluate final delivered evidence panel and neighboring shot continuity automatically; report failures without relaxing gates. Deliver to established Mac target.

Implementation additions found during preflight: automatic request repair calls the generic compiler, whose target header still hard-coded Chinese animation. Remove that header assumption and retain the existing explicit visual-style sentence; a real live-action compilation regression pins this. Align the existing local Qwen preset model spelling with the observed /v1/models response so subprocess repairs use the same working route.
