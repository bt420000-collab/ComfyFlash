"""Project opt-in for dispatching videos only with current CineSpindle evidence."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import ParamError


POLICY_FILE = ".comfyflash-project.json"
CINESPINDLE_POLICY = {"video_workflow": "cinespindle"}


def production_project(directory: Path) -> Path | None:
    """Find the closest registered film project, including through a directory link."""
    directory = directory.resolve()
    for parent in (directory, *directory.parents):
        marker = parent / POLICY_FILE
        if not marker.exists() and not marker.is_symlink():
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ParamError("PRODUCTION_POLICY_INVALID", f"无法读取影视项目规则 | {marker}: {exc}") from exc
        if data != CINESPINDLE_POLICY:
            raise ParamError("PRODUCTION_POLICY_INVALID", f"不识别的影视项目规则 | {marker}")
        return parent
    return None


def _read_evidence(value, project: Path | None) -> tuple[Path, dict]:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("提供本地逐镜计划或动作审查文件的绝对路径")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"使用绝对路径 | {path}")
    path = path.resolve()
    if project is not None and not path.is_relative_to(project):
        raise ValueError(f"把逐镜计划和动作审查保存在登记项目内 | {project}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"需要 JSON 对象 | {path}")
    return path, data


def check_video_approval(output: Path, tone: str, prompt: str, images: list[str],
                         video: str | None, approval: dict | None) -> dict | None:
    project = production_project(output.parent) or production_project(Path.cwd())
    if approval is None:
        if project is None:
            return None
        raise ParamError("PRODUCTION_APPROVAL_REQUIRED",
                         f"影视项目视频需要 CineSpindle 审查 | {project}。先运行 h3-plan，再用 h3-run 派发。")
    if not isinstance(approval, dict) or set(approval) - {"plan", "stage", "motion_review"}:
        raise ParamError("PRODUCTION_APPROVAL_INVALID", "提供 production_approval.plan、stage 和按需的 motion_review")

    try:
        from xcard.CineSpindle.mindset.h3_preproduction import build_h3_request
    except ImportError as exc:
        raise ParamError("CINESPINDLE_UNAVAILABLE", "当前 ComfyFlash 找不到本机 CineSpindle XCard") from exc
    try:
        plan_path, plan = _read_evidence(approval.get("plan"), project)
        stage = approval.get("stage")
        if stage not in ("motion", "final"):
            raise ValueError("stage 必须是 motion 或 final")
        if stage == "motion" and plan.get("route") != "R2":
            raise ValueError("仅 R2 计划可以派发动作底片")
        motion_path, motion_review = None, None
        if "motion_review" in approval:
            if stage != "final" or plan.get("route") != "R2":
                raise ValueError("仅 R2 成片阶段接受动作审查")
            motion_path, motion_review = _read_evidence(approval["motion_review"], project)
        expected = build_h3_request(plan, stage, prompt, output, motion_review=motion_review)
        approved_images = expected.get("image_in", [])
        if isinstance(approved_images, str):
            approved_images = [approved_images]
        if (tone != expected["tone"]
                or [Path(path).resolve() for path in images] != [Path(path).resolve() for path in approved_images]
                or (Path(video).resolve() if video else None)
                != (Path(expected["video_in"]).resolve() if "video_in" in expected else None)):
            raise ValueError("实际 Tone、参考图片顺序或动作底片与审过的逐镜计划不一致")
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise ParamError("PRODUCTION_APPROVAL_INVALID", f"CineSpindle 送模审查未通过 | {exc}") from exc

    receipt = {"plan": str(plan_path), "plan_id": plan["plan_id"], "route": plan["route"],
               "shot_id": plan["shot_id"], "stage": stage}
    if project is not None:
        receipt["project"] = str(project)
    if motion_path is not None:
        receipt["motion_review"] = str(motion_path)
    return receipt
