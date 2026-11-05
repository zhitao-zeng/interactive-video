# Optional SoulTuner music selection

The studio can search the existing real SoulTuner local catalogue, let its episode
Agent explicitly select a candidate, and apply it through additive assembly and
the existing delivered-movie review. No music is chosen by default. Existing
editions, native dialogue, video takes and generation limits remain unchanged.

`configs/interactive.json` points `services.music_selection_config` to a local,
Git-ignored `.runtime/music-selection/runtime.json`. This file names the existing
SoulTuner Python interpreter/source, audited local catalogue, Neo4j URI, CPU M2D
checkpoint/BERT directory, isolated data directory and named text endpoint. Reuse
the tested runtime; the studio does not install packages or download weights.
With optional `neo4j_home` and `java_home`, selection can start the configured
loopback test index under a per-index lock and restore it afterwards. An already
running service stays running; remote/shared services are never started by this
adapter. Credentials come from the project's existing environment
and are passed only through the child environment.

Required local runtime keys are `python`, `source_root`, `catalog`, `neo4j_uri`,
`m2d_checkpoint`, `bert_path`, and `data_dir`; `text_endpoint` defaults to the
existing `phanthy-ds41` preset. Record the actual pool/coverage in `catalogue_scope`.
The catalogue uses the experiment's existing audited rows: song_id, title, artist,
local_audio, source/licence/usage metadata and audio_audit_report/source_windows.
Neo4j must already contain the matching real graph and M2D index. Missing or failed
services produce a retained failure, never mock recommendations.

Pure selection without film production:

```sh
PYTHONPATH=src .venv/bin/python scripts/interactive_video.py music \
  --request /path/to/music-query.json --output-dir /path/to/owned-search-run
```

The request is `{"query":"quiet investigation, no prominent vocals or drums", "limit":3}`.
For known original IDs, add `"track_ids":["BGM-009","BGM-010"]`. This performs
explicit catalogue lookup without a fuzzy model call and still returns unreviewed
candidates. It does not claim dramatic suitability.
The raw upstream response is retained, while the exposed list is deduplicated,
restricted to real local files, capped to the requested count and marked
`unreviewed`. Gaps and uncertain fitness remain visible. Repeated identical calls
reuse that search; failed calls are retained and do not silently retry.

The episode Agent calls `select_music output/music-selection.json`. For search,
the same query/limit object additionally contains `"action":"search"`. Its real
audio evidence is copied into the sandbox. `status.music_selection.apply` gives
the exact adoption schema: returned search/candidate IDs, `review.status=accepted`,
an evidence citation/reason/limitations, verbatim source_quote, source interval
and gain_db. Unreviewed, uncertain, rejected or unregistered choices cannot apply.
An independent source compatibility check preserves explicit no-music decisions.
Use action `decline` with an uncertain/rejected reason to record the assessment
without changing adopted music or the film.

Adoption persists `delivery_policy.json.episode_music`, including source, licence,
review and interval. A PCM selection is derived without overwriting the source.
The original `apply` operation keeps one looped episode bed. For story-timed
music, use `action: apply_timeline`, with `cues` and `silences` as exposed by
status. Each cue has an accepted candidate/review, source interval, gain, fades,
an explicit loop choice and start/end shot anchors. For example, an anchor is
`{"shot":7,"edge":"start","offset_seconds":0}`. End anchors use `edge: end`;
offsets are seconds inside that shot. The host uses the same measured shot spans
as evidence labels and recomputes the music bus after reassembly. Source-authored
silences remain protected, additional gaps affect only added music, and music
does not restart in an unscored outro. Changing cue/gap inputs invalidates the old
mix. New usage goes to `.runtime/music-selection/usages.json`, separate from the
original workbook/trial history. Sound-design changes invalidate the corresponding
source approval; a cached approval cannot override a new no-music constraint.

New H3 editions with a source `music_plan` enter the existing persistent episode
Agent even without first-frame mode. Existing produced editions retain their
original take history and require their existing controller. The Agent searches,
assesses and calls `select_music`; unfinished source-planned music blocks finish.
Packing keeps source music intent for assembly while setting the native model's
non_diegetic_music to None, so native generation does not add a second score.

Existing `bgm_path` must be resolved first to avoid two music beds.
With an assembled film, the host copies the video stream,
retains native speech additively, and reruns media checks. Otherwise the policy
applies at normal assembly. Reassembly/changes restart from retained pre-mix
sources; retries do not stack the music or environment bed. Clean delivery keeps
the mixed track and selected-music provenance, and exports `music-credits.txt`.
Final movie review still runs.

The supplied `BGM曲库完整包.zip` adds24original IDs/audio/attribution files, its
unchanged workbook/catalogue and3historical trials. The active catalogue now has
61tracks with the earlier37; originals are under the ignored music-library runtime.
`scripts/import_music_library.py --archive PATH --destination PATH --work-dir PATH`
uses the existing isolated CPU runtime, samples the start/middle/end, retains the
actual Eureka analysis, indexes768dM2D features, and merges by original ID. It
does not overwrite original credits or trial records or auto-mark tracks adopted.
The runtime additionally names `audio_source_root` and `audio_model_dir` for the
already-installed Eureka source/model; it does not download them.

Catalogue coverage is limited and no Douyin popularity was verified. Index
success, rank and18-second sampled audio analysis do not establish full-song/film
listening or dramatic fit. The selection's limitations stay with its delivery.
Original attribution and the actual edits are exported in `music-credits.txt`.
