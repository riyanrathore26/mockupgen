"""Smart Object + Linked Layers handling — core of the product.

This module will eventually:
1. Locate the LINKED_LAYER* tagged blocks at document level
2. Map smart-object layers (front / left / right / back) to their embedded files
3. Extract the embedded binary (PNG / JPG / nested PSD)
4. Replace that binary with a new user image
5. Keep transforms, warps and layer effects intact
"""

from __future__ import annotations

# Placeholder — real implementation comes after layer parsing is solid.
