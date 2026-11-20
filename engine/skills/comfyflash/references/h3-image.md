# H3 Image prompt guide

Use `t3` for a new image and `t4` for editing one reference image. Send the finished description as the `prompt` string and the desired canvas separately as `resolution`. Keep an explicit user choice of canvas. For `t4`, use `match` when the edit should follow the reference framing and the user has given no other canvas.

Write natural English descriptions of visible content. Keep exact on-image wording in its original language, with its punctuation and spacing, inside quotes. Specify its location and hierarchy. State when an image should contain no lettering.

## `t3` Text to image

Describe what the finished image is and what it is for. Establish the main subjects, their positions, the viewpoint, and the layout before describing materials, lighting, and colors. Write one or more flowing paragraphs.

Example for a 4:3 market poster.

```text
A landscape 4:3 poster for a night market. At the top center, a large white title reads "周五夜市". Directly below, a smaller white line reads "18:00 开始". In the lower half, one orange paper lantern hangs over a dark wooden tea counter on the left, while the right half has open navy space. Soft amber light falls across the counter and fades into the matte navy background. The image contains only the two specified lines of text.
```

## `t4` Single-image edit

Inspect `<Picture 1>` first. Write one continuous paragraph that identifies the relevant original elements, describes the finished image and what stays, then specifies the changed detail and its location. Use `<Subject N>` when it helps track source elements with distinct roles. Describe the intended final state. Preserve unrelated objects, framing, and text as requested. Use visible cues for details that cannot be read or identified reliably.

Example for changing only the color of a cup on the left of the reference.

```text
The blue cup on the left in <Picture 1> is <Subject 1>, and the lower label reading "实验 01" is <Subject 2>. The target image keeps the original framing and object positions. <Subject 2> remains exactly "实验 01" with its original placement and spacing. <Subject 1> keeps its shape, size, position, and lighting. The cup's blue surface is now orange, with the original highlights and shadows still visible.
```

[MiniMax H3 Image prompting skill](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/SKILL.md) · [MiniMax H3 Image edit guide](https://github.com/MiniMax-AI/MiniMax-H3-Image/blob/main/skills/h3-image-prompt-writing/references/edit-en.txt)
