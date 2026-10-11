"""Download only OFL fonts from the Google Fonts source repository."""
from pathlib import Path
from urllib.request import urlretrieve

out = Path(__file__).resolve().parent / "assets" / "fonts"
out.mkdir(parents=True, exist_ok=True)
base = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
for weight in ("Regular", "Medium", "SemiBold", "Bold"):
    name = f"BeVietnamPro-{weight}.ttf"
    urlretrieve(base + "bevietnampro/" + name, out / name)
urlretrieve(base + "bevietnampro/OFL.txt", out / "BeVietnamPro-OFL.txt")
urlretrieve(base + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf", out / "JetBrainsMono.ttf")
urlretrieve(base + "jetbrainsmono/OFL.txt", out / "JetBrainsMono-OFL.txt")
print("Downloaded Be Vietnam Pro and JetBrains Mono with OFL licenses.")
