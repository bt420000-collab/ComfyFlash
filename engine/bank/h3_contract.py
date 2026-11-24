"""Shared capability declarations for the H3 recipes in this Bank."""

from copy import deepcopy

from .. import h3, media


H3_PROMPT_GUIDE = "engine/skills/comfyflash/SKILL.md"


def derived_video_tone(base, *, tone_id, name, purpose, capability, image_role,
                       prompt_format, image_control, notes, video_control=None):
    """Reuse an H3 video recipe's model files and sampling controls."""
    spec = deepcopy(base)
    spec.update(id=tone_id, name=name, purpose=purpose, notes=notes, prompt_format=prompt_format,
                inputs={"required": ["prompt", "image_in"], "optional": []})
    spec["controls"]["prompt"] = {"note": prompt_format["instruction"]}
    spec["controls"]["image_in"] = image_control
    resolution = spec["controls"]["resolution"]
    resolution["default"] = "768x448"
    if "768x448" not in resolution["presets"]:
        resolution["presets"].insert(0, "768x448")
    if video_control:
        spec["inputs"]["optional"].append("video_in")
        spec["controls"]["video_in"] = video_control
    return h3_tone(spec, capability=capability, image_role=image_role)


def h3_tone(spec, *, capability, image_role=None):
    video = spec["output"] == "video"
    fields = {"prompt": {"type": "string", "role": "prompt", "media_type": "text", "required": True}}
    controls = spec["controls"]
    controls["prompt"].update(type="string", pattern=r"\S")
    if image_role:
        image = controls["image_in"]
        fields["image_in"] = {
            "type": "path_artifact_or_list", "role": image_role, "media_type": "image",
            "required": True, "min_items": image["min_items"], "max_items": image["max_items"],
        }
        image["type"] = "path_artifact_or_list"
    if "video_in" in controls:
        controls["video_in"]["type"] = "string"
        fields["video_in"] = {
            "type": "path", "role": "reference_video", "media_type": "video", "required": False,
        }
    spec["inputs"]["fields"] = fields
    controls["seed"]["type"] = "integer"
    canvas = controls["resolution"]
    names = [*h3.VIDEO_ASPECTS, *h3.VIDEO_ALIASES] if video else [*h3.IMAGE_ASPECTS, "1k", "2k", "max"]
    canvas.update(type="string", grid=h3.CANVAS_MULTIPLE,
                  sizes={name: list(h3.resolve_canvas(spec["output"], name)) for name in names},
                  empty_alias="16:9" if video else "2k",
                  max_pixels=h3.MAX_PIXELS if video else h3.IMAGE_MAX_EDGE ** 2,
                  min_explicit_edge=256 if video else h3.CANVAS_MULTIPLE)
    if not video and image_role:
        canvas["match_short_edge"] = [h3.IMAGE_REF_SHORT_EDGE, h3.IMAGE_REF_VAE_MAX_SHORT_EDGE]
    params = spec["advanced"]
    for key, decl in params.items():
        decl["type"] = {"steps": "integer", "cfg": "number", "lora_strength": "number"}.get(key, "string")
        if "min" in decl:
            decl["coerce_numeric"] = True  # v0.1 accepted numeric text and rounded video steps.
    defaults = {key: decl["default"] for key, decl in params.items()}
    spec["prompt_format"]["guide"] = H3_PROMPT_GUIDE
    spec.update(
        schema_version="0.2", version="0.2", tags=["h3", spec["output"], capability],
        capabilities=[capability] + (["joint_audio"] if video else []),
        outputs={"media_type": spec["output"], "formats": sorted(media.VIDEO_EXT if video else media.IMAGE_EXT),
                 "audio_required": video},
        hardware_requirements={"device": "cuda", "tested_vram_gib": 16, "tested_ram_gib": 48},
        latency_class="tens_of_seconds_to_minutes" if video else "seconds_to_tens_of_seconds",
        prompt_guide_ref=H3_PROMPT_GUIDE,
    )
    if video:
        controls["duration"].update(type="number", coerce_numeric=True)
        spec["temporal"] = {
            "fps": h3.FPS, "frame_step": 17, "frame_offset": 5, "min_frames": 5,
            "duration_policy": "round_then_ceil_to_grid",
        }
        spec["presets"] = {"pika": defaults, "legacy": {**defaults, "scheduler": "simple",
                                                        "steps": 20, "sampler": "res_multistep"}}
        spec["sampling_policy"] = {
            "default_preset": "pika", "custom_preset": "legacy", "selector": "scheduler",
            "implicit_custom_keys": ["steps", "sampler", "cfg"],
            "explicit_default_key": "sampler", "fixed_controls": ["sampler", "steps"],
        }
    else:
        spec["presets"] = {"pdd": defaults}
    return spec
