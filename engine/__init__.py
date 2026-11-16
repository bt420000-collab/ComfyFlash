"""Comfy Flash H3 v0.1 —— 只装音色的播放器。

对外唯一入口：

    from engine import Flash
    with Flash() as flash:
        flash.tone_list()
        flash.tone_info("t2")
        flash.play("t2", prompt=..., image_in=..., output=...)
"""

from .core import Flash

__all__ = ["Flash"]
__version__ = "0.1.0"
