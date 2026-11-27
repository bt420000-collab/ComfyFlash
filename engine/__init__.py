"""ComfyFlash 的同步本地推理接口。

对外唯一入口：

    from engine import Flash
    with Flash() as flash:
        flash.tone_list()
        flash.tone_info("t2")
        flash.play("t2", prompt=..., image_in=..., output=...)
"""

from .core import Flash

__all__ = ["Flash"]
__version__ = "0.2.2"
