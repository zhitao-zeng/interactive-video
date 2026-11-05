# Spatial blockout and white-model planning

Use blockout for a specific spatial problem: several people sharing a room,
cross-cut seats, an entrance/exit, a prop handover, an obscured route or a moving
camera whose changing view must remain intelligible. A simple close observation
does not need a model merely because Blender is available.

Start with the actual scene reference and source. Preserve doors, windows,
furniture/landmark anchors, route connectivity and current story time. A single
picture does not establish unshown metric dimensions. Separate source facts,
reference observations and proposed staging; label unknown scale/depth explicitly.

For each chosen scene, provide:

- Stable world anchors and orientation, including entrance/exit/occlusion landmarks.
- All `placed` people/animals/objects, their positions, facing, standing/sitting
  state and current prop holders. Placed actors need not all be in every crop.
- Per-shot camera start/height/view direction/framing, intended subject, path and
  visible endpoint; distinguish actor movement from the camera's movement.
- What `in_frame` includes, excludes or partly occludes, plus the state handed to
  the next shot. Screen left/right is not a stable world coordinate.
- Assumptions and the exact shots that the proposed layout would support.

In creation-only mode, output this information in `spatial_plan`. It is a design
for later Blender/geometry work, not a model or render already made. A later
production task may render through the supported existing spatial-reference path.
Keep geometry-only evidence distinct from identity/outfit references and from an
actual first-frame condition; use only roles the current provider supports.

Recorded local experience: an old lecture-room template carried unrelated geometry,
floor seating and extras into new scenes; substitute the actual set rather than
reusing that default. A room inferred from text was rejected because it did not
match the supplied picture, then aligned to visible doors/windows/furniture.
Gray proxy figures can leak into photoreal output, and image conversion can ignore
the intended camera. In a small H3 room sample, both a revised ordinary-reference
version and a geometry-reference version retained the sampled body/chair counts;
that did not establish blockout's superiority. A later reverse-angle/standing sample
still reopened on the old wide shot and replayed standing up. A correct blockout
does not replace start/action/end continuity or actual generated-frame review.

These lessons derive from the agent-skill blockout experiment and the 2026-10-13
romance spatial-reference trials. Their room dimensions, actors and temporary
generation permissions are not defaults for another episode.
