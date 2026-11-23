# H3 Video Prompt Guide (Required)

This guide is a required prompt-writing specification for ComfyFlash `t1` and `t2`, not optional style advice.

- `t1` uses the H3 **T2VA** text-to-video prompt format.
- `t2` uses the H3 **Ref2VA** full-reference prompt format.
- Convert the user's brief into the correct final structure before every inference call. Do not pass a raw brief, a simple translation, or a keyword pile directly to H3.
- Write visual and sound descriptions in English. Preserve dialogue, lyrics, and visible scene text verbatim in their original language.
- Put `duration`, `resolution`, and `seed` in the ComfyFlash request controls, not in place of the model's prompt structure.
- The only bypass is an explicit user instruction to pass their prompt verbatim. Treat that as an intentional override, and do not claim that the verbatim text has been converted to the official format.

Read the official upstream skill when updating this guide:
[H3 Prompt Writing Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md) ·
[Base-mode guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt) ·
[Full-reference guide](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)

## Shared rules for both modes

1. Describe the actual video in playback order. `[Shot 1]` is the opening shot and has no timestamp. Number later shots sequentially and give each cut a strictly increasing timestamp inside the requested duration, for example `[Shot 2] At 00:03.500, ...`.
2. Begin `[Shot 1]` with the overall visual style and initial composition. Establish subject appearance, position, environment, key props, action and state changes.
3. Describe camera movement naturally within the shot. When useful, specify movement type, amplitude and speed, such as `The camera pushes in with small amplitude at slow speed`. Do not add a cut merely to change framing slightly.
4. Give every speaking, singing or off-screen vocal source a stable ID such as `(S1)`. Keep that ID consistent across shots. Put the speaker description, ID and delivery outside `<d>`; inside `<d>`, put only the language tag and the exact spoken or sung words, for example `<d>[Chinese] 晚上好！</d>`. For voiceover, use the phrase `says in an off-screen voiceover` and immediately state after the `<d>` block that the corresponding on-screen character's lips remain closed.
5. Preserve dialogue, lyrics, punctuation and visible wording exactly. Do not translate or rewrite user-supplied lines. Put visible signs, labels, subtitles and other on-screen wording in double quotes and state their placement.
6. When dialogue or lyrics continue across a cut, use `<scenetrans>` at both connecting points and explicitly say the audio continues across the cut. Use `<cutoff>` when speech is intentionally truncated at the end.
7. `overall_soundscape` summarizes ambience, physical action sounds and non-verbal human sounds in 1–4 English sentences. Use `N/A` only if the user explicitly requests complete silence throughout the video.
8. `non_diegetic_music` describes audience-only background score in 1–3 English sentences. Use `N/A` when no non-diegetic score is wanted. Music audible to characters belongs in the main audiovisual description.

## `t1` Text to video (T2VA)

The final prompt must contain these three fields in this exact order. Do not add a reference-image alignment instruction because `t1` has no reference image.

```text
integrated_multimodal_description: [Shot 1] <English visual, action, camera, dialogue and synchronized diegetic-audio timeline>

overall_soundscape: <English ambience, physical-action sounds and non-verbal vocal sounds>

non_diegetic_music: <English audience-only score description, or N/A>
```

Write the complete scene from the user's brief. Add concrete details that help the model stage the requested action, but do not invent extra plot turns, characters, props or text that change the intent. Keep the described action and shot timings within the requested `duration`.

Example:

```text
integrated_multimodal_description: [Shot 1] Live-action, a fixed medium shot frames a bicycle mechanic (S1) at a workbench under a warm overhead lamp. The mechanic lowers a wrench onto the bench with a metallic clink, looks up and says <d>[Chinese] 修好了。</d> The mechanic slides the repaired wheel forward with both hands.

overall_soundscape: A quiet fan hums throughout the shot. Faint street traffic is audible outside the workshop.

non_diegetic_music: N/A
```

## `t2` Single-image reference video (Ref2VA)

The final prompt must contain these six sections in this exact order:

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

Do not collapse these sections into a single natural-language paragraph.

### Reference labels

- Use `<Subject N>` for reusable visible content such as a person, animal, object, scene, clothing, prop, style or pose.
- Use `<Picture 1>` for the supplied image. If it only provides a character, scene, costume or style reference, cite it inside the relevant `<Subject N>` definition; do not create a separate standalone picture definition.
- Create a standalone `<Picture 1>` definition when the image itself is a concrete first/last frame, keyframe or composition anchor.
- Keep every label's meaning stable in all six sections. Do not introduce new labels in `summary`.

### Section requirements

- `subject_definitions`: define each reusable subject on its own line and cite `<Picture 1>` as the source where relevant.
- `summary`: one short English paragraph beginning with the task-type prefix that matches the actual use, usually `[reference generation]`; use `[keyframe completion]` only when the image is a concrete target-frame anchor. Summarize the intended video and reference relationships.
- `retention_analysis`: include one line for each defined reference label, with its role and one official relationship marker: `fully_preserved`, `partially_preserved`, `attribute_transfer`, or `weak_reference`. Do not call newly added scene elements a loss of reference fidelity.
- `detailed_description`: describe each shot in playback order, including composition, subject appearance and position, environment, lighting, action, state changes, camera movement, sound and where the reference actually applies. Use the shared shot, dialogue and sound rules above.
- `overall_soundscape` and `non_diegetic_music`: follow the shared rules above.

Example:

```text
subject_definitions:
<Subject 1> is the woman standing beside the tea stall in <Picture 1>.

summary:
[reference generation] <Subject 1> greets the camera at the tea stall in a single shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - Her facial appearance and clothing are retained from <Picture 1>.

detailed_description:
[Shot 1] Live-action, a natural handheld medium shot at an evening tea stall. <Subject 1> stands beside the counter, lifts one hand and says <d>[Chinese] 晚上好！</d>. She lowers her hand after the greeting. The camera stays at eye level with slight handheld movement.

overall_soundscape: Low market chatter continues behind the greeting, with an occasional quiet clink from the tea stall.

non_diegetic_music: N/A
```

## Before sending the prompt

Check all of the following:

- Correct mode selected: T2VA for `t1`, Ref2VA for `t2`.
- Exact field names and section order are present.
- Shot numbering and timestamps are sequential and fit the requested duration.
- Dialogue, lyrics and visible text remain exact and in the original language.
- Reference labels are defined and used consistently.
- Camera, action, sound and continuity are described concretely rather than as a plot summary.
