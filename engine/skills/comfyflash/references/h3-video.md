# H3 Video prompt guide

Use `t1` for text to video (T2VA) and `t2` for video from one reference image (Ref2VA). The examples below are ready to use as the `prompt` string. Set `duration` and `resolution` in the request and keep the described action within that duration.

Write visual and sound descriptions in English. Preserve dialogue, lyrics, and visible lettering in their original language and exact wording. Describe shots in playback order. Start with `[Shot 1]` and label later cuts with their start times, as in `[Shot 2] At 00:03.000, …`. For each shot, describe the framing, subject position, actions, camera movement, and sounds synchronized to the action. Give speakers stable IDs such as `(S1)` and put spoken words inside `<d>[Chinese] …</d>`. Summarize continuous ambience separately. Use `N/A` for `non_diegetic_music` when the brief calls for no background score.

## `t1` Text to video

Use `integrated_multimodal_description`, `overall_soundscape`, and `non_diegetic_music` in that order. Write the entire scene from the brief. Keep the number of actions and cuts realistic for the requested length.

Example for a five-second clip with one shot and a line of dialogue.

```text
integrated_multimodal_description: [Shot 1] A fixed medium shot shows a bicycle mechanic (S1) at a workbench under a warm overhead lamp. The mechanic lowers a wrench onto the bench with a metallic clink, looks up, and says <d>[Chinese] 修好了。</d> The mechanic then slides the repaired wheel forward with both hands.

overall_soundscape: A quiet fan hums throughout the shot. Faint street traffic is audible outside the workshop.

non_diegetic_music: N/A
```

## `t2` Single-image reference video

Inspect `<Picture 1>` and define the reference subjects used in the video. State which visible characteristics carry over and where those subjects appear. Use the Ref2VA section order shown below. Mention the reference image as a frame or composition anchor only when the brief assigns it that role. The request's `image_in` supplies the single reference image.

Example for a six-second clip using a reference photo of a woman at a tea stall.

```text
subject_definitions:
<Subject 1> is the woman standing beside the tea stall in <Picture 1>.

summary:
[reference generation] <Subject 1> greets the camera at the tea stall in a single shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - Her facial appearance and clothing are retained from <Picture 1>.

detailed_description:
A natural handheld shot at an evening tea stall. [Shot 1] <Subject 1> (S1) stands beside the counter in a medium shot. She lifts one hand, waves toward the camera, and says <d>[Chinese] 晚上好！</d> She lowers her hand after the greeting. The camera stays at eye level with slight handheld movement.

overall_soundscape: Low market chatter continues behind the greeting, with an occasional quiet clink from the tea stall.

non_diegetic_music: N/A
```

[MiniMax H3 base video guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md) · [MiniMax H3 full-reference video guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)
