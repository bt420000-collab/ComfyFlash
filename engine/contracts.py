"""Machine-readable Tone request contracts and the rules that resolve them."""

import hashlib
import json
import math
import re

from . import h3


class ParamError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def scalar_schema(decl):
    keys = {"type": "type", "default": "default", "choices": "enum", "min": "minimum",
            "max": "maximum", "note": "description", "pattern": "pattern"}
    prop = {target: decl[source] for source, target in keys.items() if source in decl}
    if decl.get("coerce_numeric"):
        prop["x-numeric-normalization"] = "round_half_even" if decl["type"] == "integer" else "number"
    return prop


def scalar_value(key, value, decl, code="BAD_PARAM"):
    """Validate the canonical value after applying a declared legacy coercion."""
    typ = decl["type"]
    if typ in ("number", "integer"):
        if isinstance(value, bool):
            raise ParamError(code, f"{key} must be {typ}")
        if decl.get("coerce_numeric"):
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ParamError(code, f"{key} must be {typ}") from None
        if not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise ParamError(code, f"{key} must be a finite {typ}")
        if ("min" in decl and value < decl["min"]) or ("max" in decl and value > decl["max"]):
            raise ParamError(code, f"{key} outside supported range [{decl.get('min')}, {decl.get('max')}]")
        if typ == "integer" and not isinstance(value, int):
            if not decl.get("coerce_numeric") and not value.is_integer():
                raise ParamError(code, f"{key} must be an integer")
            value = round(value)
    elif typ == "string":
        if not isinstance(value, str) or ("pattern" in decl and not re.search(decl["pattern"], value)):
            raise ParamError(code, f"{key} must be a valid string")
    elif typ == "boolean" and not isinstance(value, bool):
        raise ParamError(code, f"{key} must be boolean")
    if "choices" in decl and value not in decl["choices"]:
        raise ParamError(code, f"{key} must be one of {decl['choices']}, got {value!r}")
    return value


def delivery_controls(spec):
    declarations = {
        "overwrite": {"type": "boolean", "default": False, "note": "Replace an existing output with permission."},
        "request_id": {"type": "string", "pattern": r"\S", "note": "Optional caller-supplied request identifier."},
    }
    if spec["output"] == "video":
        declarations["inspection_frames"] = {"type": "integer", "default": 6, "min": 1, "max": 24,
                                              "note": "Number of samples for the contact sheet."}
    return declarations


def resolve_delivery(spec, provided):
    resolved = {}
    for key, decl in delivery_controls(spec).items():
        if key in provided or "default" in decl:
            resolved[key] = scalar_value(key, provided.pop(key, decl.get("default")), decl)
    return resolved


def canvas_pattern(decl):
    def case_insensitive(name):
        return "".join(f"[{letter.lower()}{letter.upper()}]" if letter.isascii() and letter.isalpha()
                       else re.escape(letter) for letter in name)

    names = list(decl.get("sizes", {}))
    if "match_short_edge" in decl:
        names.append("match")
    valid = [case_insensitive(name) for name in names]
    valid.append(r"\d+\s*[xX×*]\s*\d+")
    whitespace = r"\s*(?:" + "|".join(valid) + r")\s*"
    pattern = f"(?:{whitespace}|)" if "empty_alias" in decl else whitespace
    return "^" + pattern + r"$(?![\s\S])"


def request_schema(spec):
    """Canonical JSON request. x-* rules describe host filesystem and normalization."""
    inputs = spec["inputs"]
    props = {"tone": {"type": "string", "const": spec["id"]},
             "seed": {"type": ["integer", "null"], "default": None},
             "output": {"type": "string", "minLength": 1, "x-path": "absolute-output",
                        "x-extensions": spec["outputs"]["formats"]},
             "verbose": {"type": ["boolean", "null"]}, "audio_in": {"type": "null"}}
    if "image_in" not in inputs.get("fields", {}):
        props["image_in"] = {"type": "null"}
    for key, decl in {**spec.get("controls", {}), **spec.get("advanced", {}), **delivery_controls(spec)}.items():
        if key == "image_in":
            field = inputs["fields"][key]
            path = {"type": "string", "minLength": 1, "x-path": "absolute-readable-image"}
            image = {"oneOf": [path, {"type": "object", "required": ["media_type", "path"],
                                     "properties": {"media_type": {"const": "image"}, "path": path}}]}
            forms = [{"type": "array", "items": image, "minItems": field["min_items"], "maxItems": field["max_items"]}]
            if field["min_items"] <= 1 <= field["max_items"]:
                forms.insert(0, image)
            props[key] = {"oneOf": forms, "x-role": field["role"]}
        else:
            prop = scalar_schema(decl)
            if key in ("resolution", "duration", "seed"):
                prop["type"] = [decl["type"], "null"]
            if key == "resolution":
                prop["pattern"] = canvas_pattern(decl)
                prop["x-canvas"] = {k:v for k,v in decl.items() if k not in ("type","default")}
            props[key] = prop
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
              "type": "object", "properties": props, "additionalProperties": False,
              "required": ["tone", *inputs["required"], "output"]}
    if "temporal" in spec:
        props["fps"] = {"type": ["number", "null"], "enum": [spec["temporal"]["fps"], None]}
        schema["x-temporal"] = spec["temporal"]
    else:
        props.update(duration={"type": "null"}, fps={"type": "null"})
    if "sampling_policy" in spec:
        policy = spec["sampling_policy"]
        preset = spec["presets"][policy["default_preset"]]
        key = policy["selector"]
        schema["allOf"] = [{"if": {"required": [key], "properties": {key: {"const": preset[key]}}},
                            "then": {"properties": {k: {"const": preset[k]} for k in policy["fixed_controls"]}}}]
        schema["x-sampling-policy"] = {**policy, "presets": spec["presets"]}
    return schema


def schema_hash(schema):
    data = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def resolve_advanced(spec, provided):
    decls = spec.get("advanced", {})
    unknown = provided.keys() - decls.keys()
    if unknown:
        raise ParamError("UNSUPPORTED_CONTROL", f"{spec['name']} does not accept {', '.join(sorted(unknown))}")
    params = {k: scalar_value(k, provided.get(k, decl["default"]), decl) for k, decl in decls.items()}
    policy = spec.get("sampling_policy")
    if not policy:
        return params
    default = spec["presets"][policy["default_preset"]]
    custom = spec["presets"][policy["custom_preset"]]
    selector = policy["selector"]
    fixed = policy["fixed_controls"]
    matches_default = all(params[k] == default[k] for k in fixed)
    if selector not in provided and provided.keys() & set(policy["implicit_custom_keys"]):
        if policy["explicit_default_key"] not in provided or not matches_default:
            params[selector] = custom[selector]
    if params[selector] == default[selector]:
        if not matches_default:
            raise ParamError("BAD_PARAM", f"{selector}={default[selector]} requires " +
                             ", ".join(f"{k}={default[k]}" for k in fixed))
    else:
        for key in fixed:
            if key not in provided:
                params[key] = custom[key]
    return params


def resolve_time(spec, duration, fps):
    if "temporal" not in spec:
        if duration is not None or fps is not None:
            raise ParamError("UNSUPPORTED_CONTROL", f"{spec['name']} does not accept duration/fps")
        return {}
    grid = spec["temporal"]
    rate = grid["fps"]
    if fps is not None and (isinstance(fps, bool) or fps != rate):
        raise ParamError("BAD_PARAM", f"{spec['name']} requires fps={rate}, got {fps!r}")
    decl = spec["controls"]["duration"]
    seconds = scalar_value("duration", decl["default"] if duration is None else duration, decl, "BAD_DURATION")
    requested = round(seconds * rate)
    if grid["duration_policy"] != "round_then_ceil_to_grid":
        raise ParamError("BAD_DURATION", f"Unknown duration policy {grid['duration_policy']}")
    lower = max(requested, grid["min_frames"])
    aligned = lower + (grid["frame_offset"] - lower) % grid["frame_step"]
    if "max_frames" in grid and aligned > grid["max_frames"]:
        raise ParamError("BAD_DURATION", f"Aligned frame count {aligned} exceeds limit {grid['max_frames']}")
    return {"requested_duration_s": seconds, "requested_frame_count": requested,
            "aligned_frame_count": aligned, "fps": rate}


def resolve_canvas(spec, value, image_size):
    decl = spec["controls"]["resolution"]
    try:
        name = decl.get("empty_alias", "") if value == "" else value.strip().lower()
        if name in decl.get("sizes", {}):
            return tuple(decl["sizes"][name])
        if name == "match" and "match_short_edge" in decl and image_size:
            return h3.size_short_edge(*image_size, *decl["match_short_edge"])
        match = re.fullmatch(r"(\d+)\s*[x×*]\s*(\d+)", name)
        if not match:
            raise ValueError("Expected positive WxH dimensions or a declared size")
        w, h = map(int, match.groups())
        grid = decl.get("grid", 1)
        minimum = decl.get("min_explicit_edge", 1)
        if w % grid or h % grid or min(w, h) < minimum:
            raise ValueError(f"Dimensions must be multiples of {grid}, each at least {minimum}")
        if "max_pixels" in decl and w * h > decl["max_pixels"]:
            raise ValueError(f"{w}x{h} exceeds the {decl['max_pixels']}-pixel limit")
        return w, h
    except (TypeError, ValueError) as exc:
        raise ParamError("BAD_RESOLUTION", str(exc)) from exc
