# H3 Image Prompt Guide (Required)

This guide is a required prompt-writing specification for ComfyFlash `t3` and `t4`, not optional style advice.

- `t3` uses the MiniMax H3-Image text-to-image writing format.
- `t4` uses the MiniMax H3-Image reference-image editing format.
- Convert the user's brief into the matching final description before every inference call. Do not send a raw brief, a simple translation, or a keyword list directly to the model.
- Write visual descriptions in English. Preserve exact on-image wording in its original language, including punctuation and spacing.
- Keep the requested canvas in the separate `resolution` control.
- The only bypass is an explicit user instruction to pass their prompt verbatim. Treat that as an intentional override, and do not claim that the verbatim text follows the official writing guide.

Official upstream references:
[H3-Image Prompt Writing Skill](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/SKILL.md) ·
[Official prompting tips](https://github.com/MiniMax-AI/MiniMax-H3-Image#prompting-tips) ·
[Text-to-image guide](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/references/t2i-en.txt) ·
[Image-edit guide](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/references/edit-en.txt)

## Shared rules

1. Write a complete description of the finished image, not a list of tags or instructions for another writer.
2. State the task and intended artifact first, then the layout, subjects and their positions, then the visual style through concrete color, material, lighting and texture.
3. Quote every word that must appear in the image. Preserve the exact original language, punctuation, spacing, hierarchy and line breaks, and describe its location.
4. If no lettering is wanted, explicitly say that the image contains no text, letters or numbers.
5. For charts, educational images and infographics, provide exact values, units, relationships and labels. Do not invent real data.
6. Treat named entities, counts, directions and spatial relationships as constraints. Avoid contradictory instructions and vague quality words where a visible detail can be specified. For grids, state rows and columns explicitly, such as `2 rows and 3 columns`, rather than an ambiguous `2×3`.

## `t3` Text to image

Write one or more flowing English paragraphs describing the finished image.

Recommended order:

1. Purpose and deliverable: what the image is and what it is for.
2. Overall layout: aspect ratio, number of subjects or regions, viewpoint, positions and negative space.
3. Main content: appearance, pose, actions, object relationships and required information.
4. Visual treatment: style, palette, material, lighting, texture and depth.
5. Exact visible wording: quote each line and state its placement and hierarchy, or explicitly prohibit text.

Example:

```text
A landscape 4:3 poster for a night market. At the top center, a large white title reads "周五夜市". Directly below, a smaller white line reads "18:00 开始". In the lower half, one orange paper lantern hangs over a dark wooden tea counter on the left, while the right half has open navy space. Soft amber light falls across the counter and fades into the matte navy background. The image contains only the two specified lines of text.
```

## `t4` Single-image edit

Inspect the supplied image first. Write exactly one continuous English paragraph in this order:

1. Source definitions: identify the relevant source image and any elements that need stable labels.
2. Target overview and relationships: state the intended finished image, what stays, and which source elements contribute to it.
3. Changed or new details: describe each change precisely, where it occurs, and what fills any removed object's space.

Rules:

- Refer to the single supported input image as `<Picture 1>`. Do not infer hidden details or unreadable wording from the source.
- Use `<Subject N>` when separate source elements need stable identities across the description.
- Describe the final state, not a vague editing command.
- Explicitly preserve unrelated objects, positions, framing, lighting and text when the user wants them retained.
- Separate what changes from what stays. Do not let a local edit accidentally rewrite the whole image.
- Do not print section headings inside the final edit prompt.

Example:

```text
The blue cup on the left in <Picture 1> is <Subject 1>, and the lower label reading "实验 01" is <Subject 2>. The target image keeps the original framing and object positions. <Subject 2> remains exactly "实验 01" with its original placement and spacing. <Subject 1> keeps its shape, size, position and lighting. The cup's blue surface is now orange, with the original highlights and shadows still visible.
```

## Before sending the prompt

Check that the prompt describes the finished image, the composition is unambiguous, all visible wording is exact, and each requested change is separated from the content that must remain unchanged.
