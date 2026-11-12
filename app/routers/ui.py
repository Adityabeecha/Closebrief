"""HTTP routes moved from app.main: ui."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


_UI_INDEX = Path(__file__).resolve().parent.parent.parent / "ui" / "web" / "index.html"


@router.get("/", include_in_schema=False)
def ui_home() -> FileResponse:
    # Public home page. Old deep links (/#metric=...) are forwarded to /app by
    # the page itself, since the fragment never reaches the server.
    return FileResponse(_UI_INDEX.parent / "home.html", headers={"Cache-Control": "no-cache"})


@router.get("/app", include_in_schema=False)
def ui_root() -> FileResponse:
    # no-cache: browsers must revalidate so UI fixes are picked up immediately
    # (stale cached copies of this shell caused hard-to-debug auth errors).
    return FileResponse(_UI_INDEX, headers={"Cache-Control": "no-cache"})


@router.get("/samples/fpa.csv", include_in_schema=False)
def sample_fpa_csv() -> FileResponse:
    # The synthetic sample export behind "Try it with sample data" on a new
    # workspace. Public on purpose: it is the same file the repo ships.
    return FileResponse(_UI_INDEX.parent.parent.parent / "data" / "sample_fpa.csv", media_type="text/csv",
                        filename="sample_fpa.csv")


@router.get("/privacy", include_in_schema=False)
def privacy_page() -> FileResponse:
    return FileResponse(_UI_INDEX.parent / "privacy.html", headers={"Cache-Control": "no-cache"})


@router.get("/terms", include_in_schema=False)
def terms_page() -> FileResponse:
    return FileResponse(_UI_INDEX.parent / "terms.html", headers={"Cache-Control": "no-cache"})


@router.get("/og.png", include_in_schema=False)
def share_image() -> FileResponse:
    # The link-preview card for the home page (og:image / twitter:image).
    return FileResponse(_UI_INDEX.parent / "brand" / "og.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/theme.css", include_in_schema=False)
def ui_theme() -> FileResponse:
    # Design tokens shared by the home page and the app.
    return FileResponse(_UI_INDEX.parent / "theme.css", media_type="text/css",
                        headers={"Cache-Control": "no-cache"})


_UI_VENDOR = _UI_INDEX.parent / "vendor"


@router.get("/app.js", include_in_schema=False)
def ui_appjs() -> FileResponse:
    # The app's own script, split out of index.html. no-cache like the shell so
    # UI fixes are picked up immediately (they ship together).
    return FileResponse(_UI_INDEX.parent / "app.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/vendor/{filename:path}", include_in_schema=False)
def ui_vendor(filename: str) -> FileResponse:
    path = (_UI_VENDOR / filename).resolve()
    if _UI_VENDOR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    # Windows mimetypes lacks woff2; wrong MIME makes browsers reject the font.
    media_type = "font/woff2" if path.suffix == ".woff2" else None
    return FileResponse(path, media_type=media_type)
