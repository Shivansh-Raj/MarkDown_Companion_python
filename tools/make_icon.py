"""Generate assets/icons/app.ico (+png) from the programmatic pixel icon. Run once; output is committed."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.pixel_icon import pixel_image  # noqa: E402

QApplication([])
out = Path(__file__).resolve().parents[1] / "assets" / "icons"
out.mkdir(parents=True, exist_ok=True)
img = pixel_image(256)
print("png", img.save(str(out / "app.png")), "ico", img.save(str(out / "app.ico")))
