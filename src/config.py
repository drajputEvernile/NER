"""Shared pipeline settings."""

# Used by the splitter when turning PDF, TIF, and TIFF pages into JPEG files.
# Maximum DPI: originals below this keep their own DPI; higher ones are scaled down (never cropped).
Image_DPI = 250

# Used by OCR as RapidOCR max_side_len and the pre-OCR size cap.
# If a page is larger than this, the whole page is scaled down (never cropped) and then OCR'd.
Image_Scale = 4000
