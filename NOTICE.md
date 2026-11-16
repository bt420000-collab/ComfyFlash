# Source and license notices

ComfyFlash source is distributed under the GNU General Public License, version 3. See [LICENSE](LICENSE).

- [ComfyUI at 0228a9d73](https://github.com/Comfy-Org/ComfyUI/tree/0228a9d73) is the upstream inference implementation, licensed under GPL-3.0. The installed ComfyUI checkout is used at runtime.
- `engine/h3.py` adapts conditioning and latent geometry from `comfy_extras/nodes_minimax_h3.py`. `engine/runtime.py` adapts dynamic VRAM initialization and stage cleanup from upstream `main.py` and `execution.py`. The ComfyFlash adaptations and documented changes date to 2026-11-16.
- MiniMax H3 and the Pika Flex LoRA checkpoints are separately obtained runtime assets. Consult the original model and LoRA license notices supplied by their distributors and recorded in checkpoint metadata where available. This repository contains the runner and configuration example.
