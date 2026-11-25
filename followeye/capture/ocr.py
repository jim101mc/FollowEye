from __future__ import annotations

import asyncio

import numpy as np


class WindowsOcr:
    """Built-in Windows OCR (Windows.Media.Ocr): no extra install, uses the user's display languages."""

    def __init__(self) -> None:
        from winrt.windows.media.ocr import OcrEngine

        self._engine = OcrEngine.try_create_from_user_profile_languages()

    @property
    def available(self) -> bool:
        return self._engine is not None

    def recognize(self, bgra: np.ndarray) -> str:
        if self._engine is None:
            return ""
        return asyncio.run(self._recognize(np.ascontiguousarray(bgra)))

    async def _recognize(self, bgra: np.ndarray) -> str:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        limit = OcrEngine.max_image_dimension
        h, w = bgra.shape[:2]
        if max(h, w) > limit:
            step = int(np.ceil(max(h, w) / limit))
            bgra = np.ascontiguousarray(bgra[::step, ::step])
            h, w = bgra.shape[:2]
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        result = await self._engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)
