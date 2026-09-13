"""The fonts PDF export renders with.

DejaVu ships with the app rather than being looked up in the operating system,
so an exported summary comes out identical on a Linux box without
fonts-dejavu-core, a Mac and a Windows PC."""

from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"


def test_the_dejavu_fonts_ship_with_the_app():
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "LICENSE-DejaVu.txt"):
        assert (FONTS_DIR / name).is_file(), f"backend/fonts/{name} is missing"


def test_the_bundled_fonts_win_over_the_system_ones():
    """They are first in the candidate lists, so a machine that also has DejaVu
    installed still renders from the same files as one that does not."""
    import pdf_export

    for candidates in (pdf_export._DEJAVU_CANDIDATES, pdf_export._DEJAVU_BOLD_CANDIDATES):
        chosen = pdf_export._find(candidates)
        assert chosen is not None, "no DejaVu font was found at all"
        assert Path(chosen).parent == FONTS_DIR.resolve()
