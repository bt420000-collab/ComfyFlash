"""Bank —— 自动发现引擎支持的所有 Tone。

每个 ``t*.py`` 必须提供两样东西：
    TONE  机器可读的能力声明（名称、输入、旋钮、范围、默认值、依赖模型）
    play(run)  真实推理调用

``tone_list()`` / ``tone_info()`` 全部由这里的声明生成，不另外维护工具清单。
"""

from __future__ import annotations

import importlib
import pkgutil

__all__ = ["TONES"]


def _discover() -> dict:
    tones = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.name.startswith("t"):
            continue
        module = importlib.import_module("{}.{}".format(__name__, info.name))
        spec = getattr(module, "TONE", None)
        if not spec:
            continue
        if not callable(getattr(module, "play", None)):
            raise ImportError("{} 声明了 TONE 但没有 play(run)".format(info.name))
        tone_id = spec["id"]
        if tone_id in tones:
            raise ImportError("Tone id 重复：{}".format(tone_id))
        tones[tone_id] = module
    return tones


TONES = _discover()
