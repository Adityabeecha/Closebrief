"""Rendered-layout regression tests for the single-page web UI."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

WEB_ROOT = Path(__file__).parents[1] / "ui" / "web"


def _chrome_path() -> str | None:
    candidates = (
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _probe_page(width: int, target: str) -> bytes:
    return f"""<!doctype html>
<meta charset="utf-8">
<iframe id="site" src="/index.html" style="width:{width}px;height:1200px;border:0"></iframe>
<pre id="result">waiting</pre>
<script>
const frame = document.getElementById('site');
frame.addEventListener('load', () => {{
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  let result;
  if ('{target}' === 'login') {{
    win.showLogin();
    const gate = doc.getElementById('login-gate');
    const left = doc.querySelector('.lg-left');
    const right = doc.querySelector('.lg-right');
    result = {{
      viewportWidth: doc.documentElement.clientWidth,
      scrollWidth: doc.documentElement.scrollWidth,
      gateDisplay: win.getComputedStyle(gate).display,
      leftDisplay: win.getComputedStyle(left).display,
      rightWidth: Math.ceil(right.getBoundingClientRect().width)
    }};
  }} else if ('{target}' === 'drawer') {{
    win.hideLogin();
    const trigger = doc.querySelector('.hamb');
    const drawer = doc.querySelector('.side');
    trigger.click();
    const focusables = [...drawer.querySelectorAll("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])")]
      .filter(el => !el.disabled && el.getClientRects().length);
    focusables.at(-1).focus();
    doc.dispatchEvent(new win.KeyboardEvent('keydown', {{key:'Tab',bubbles:true,cancelable:true}}));
    const tabWrapsForward = doc.activeElement === focusables[0];
    focusables[0].focus();
    doc.dispatchEvent(new win.KeyboardEvent('keydown', {{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}}));
    const tabWrapsBackward = doc.activeElement === focusables.at(-1);
    const opened = {{
      expanded: trigger.getAttribute('aria-expanded'),
      label: trigger.getAttribute('aria-label'),
      hidden: drawer.getAttribute('aria-hidden'),
      inert: drawer.inert,
      focusedInside: drawer.contains(doc.activeElement),
      tabWrapsForward,
      tabWrapsBackward
    }};
    doc.dispatchEvent(new win.KeyboardEvent('keydown', {{key:'Escape',bubbles:true}}));
    const closed = {{
      bodyOpen: doc.body.classList.contains('sidebar-open'),
      expanded: trigger.getAttribute('aria-expanded'),
      hidden: drawer.getAttribute('aria-hidden'),
      inert: drawer.inert,
      focusRestored: doc.activeElement === trigger
    }};
    trigger.click();
    const navButton = drawer.querySelector('[data-view="dashboard"]');
    navButton.focus();
    navButton.click();
    result = {{
      opened,
      closed,
      navigationClose: {{
        bodyOpen: doc.body.classList.contains('sidebar-open'),
        drawerInert: drawer.inert,
        focusOutsideDrawer: !drawer.contains(doc.activeElement),
        focusRestored: doc.activeElement === trigger
      }}
    }};
  }} else {{
    win.hideLogin();
    doc.getElementById('cards').innerHTML = `<article class="card">
      <div class="card-name">Enterprise recurring revenue with a representative long metric name</div>
      <div class="card-value-row"><strong class="card-value">$12,345,678</strong><span class="delta good">+12.4%</span></div>
      <p class="narr">Representative commentary that verifies card wrapping at narrow widths without clipping its actions or values.</p>
    </article>`;
    doc.getElementById('rq-list').innerHTML = `<article class="rq-item">
      <div class="rq-main"><div class="rq-metric">Representative review metric</div>
      <div class="rq-preview">A long narrative preview that must remain reachable on a narrow display.</div></div>
      <button class="btn">Review</button>
    </article>`;
    doc.getElementById('ctx-rows').innerHTML = `<tr><td>Policy</td><td>Representative context body</td>
      <td>Revenue</td><td>2026-08</td><td><button class="row-del">Delete</button></td></tr>`;
    doc.getElementById('admin-rows').innerHTML = `<tr><td>finance@example.com</td><td>representative-user-id</td>
      <td><select><option>Administrator</option></select></td><td>2026-08-16</td></tr>`;
    const sections = [...doc.querySelectorAll('main .wrap')];
    const viewLayouts = sections.map(section => {{
      sections.forEach(el => el.style.display = el === section ? 'block' : 'none');
      const controls = [...section.querySelectorAll('button,input,select,textarea,a[href]')]
        .filter(el => {{ const r=el.getBoundingClientRect(); return r.width>0 && r.height>0; }});
      const controlFits = controls.every(el => {{
        const rect=el.getBoundingClientRect();
        if(rect.left>=-1 && rect.right<=doc.documentElement.clientWidth+1)return true;
        for(let p=el.parentElement;p&&p!==section;p=p.parentElement){{
          const style=win.getComputedStyle(p);
          if(/auto|scroll/.test(style.overflowX) && p.scrollWidth>p.clientWidth)return true;
        }}
        return false;
      }});
      return {{
        id:section.id,
        fits:section.scrollWidth<=section.clientWidth+1,
        controlsFit:controlFits
      }};
    }});
    sections.forEach(el => el.style.display = 'block');
    const columnCount = selector =>
      win.getComputedStyle(doc.querySelector(selector)).gridTemplateColumns.split(' ').length;
    result = {{
      viewportWidth: doc.documentElement.clientWidth,
      scrollWidth: doc.documentElement.scrollWidth,
      appDisplay: win.getComputedStyle(doc.getElementById('app-root')).display,
      analyticsColumns: columnCount('.an-grid'),
      formColumns: [...doc.querySelectorAll('.ctx-form')].map(el =>
        win.getComputedStyle(el).gridTemplateColumns.split(' ').length),
      sidebarPosition: win.getComputedStyle(doc.querySelector('.side')).position,
      mobileHeaderDisplay: win.getComputedStyle(doc.querySelector('.mtop')).display,
      viewLayouts
    }};
  }}
  document.getElementById('result').textContent = JSON.stringify(result);
}});
</script>""".encode()


@contextmanager
def _ui_server():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

        def log_message(self, *_args):
            return

        def do_GET(self):  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            if parsed.path == "/probe":
                query = parse_qs(parsed.query)
                body = _probe_page(int(query["width"][0]), query["target"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@lru_cache
def _render_probe(width: int, target: str) -> dict:
    browser = _chrome_path()
    if not browser:
        pytest.skip("Chrome, Chromium, or Edge is required for rendered responsive tests")
    with _ui_server() as base_url, tempfile.TemporaryDirectory() as profile:
        proc = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=5000",
                "--window-size=1400,1400",
                "--dump-dom",
                f"{base_url}/probe?width={width}&target={target}",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    marker = '<pre id="result">'
    assert marker in proc.stdout, proc.stderr
    payload = proc.stdout.split(marker, 1)[1].split("</pre>", 1)[0]
    return json.loads(payload.replace("&quot;", '"'))


@pytest.mark.parametrize("width", [320, 375, 768, 900])
def test_application_shell_uses_mobile_navigation_without_page_overflow(width):
    """Catches fixed/flex desktop layout leaking into phone and tablet viewports."""
    layout = _render_probe(width, "app")

    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert layout["appDisplay"] == "block"
    assert layout["sidebarPosition"] == "fixed"
    assert layout["mobileHeaderDisplay"] == "flex"


@pytest.mark.parametrize("width", [901, 1024, 1440, 1920])
def test_application_shell_retains_desktop_layout_without_page_overflow(width):
    """Catches mobile shell rules leaking into laptop and wide desktop layouts."""
    layout = _render_probe(width, "app")

    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert layout["appDisplay"] == "flex"
    assert layout["sidebarPosition"] == "static"
    assert layout["mobileHeaderDisplay"] == "none"


@pytest.mark.parametrize("width", [320, 375, 768, 1024, 1440, 1920])
def test_each_application_view_keeps_content_and_controls_reachable(width):
    """Catches a single page or control escaping its viewport without local scrolling."""
    layout = _render_probe(width, "app")

    assert all(view["fits"] for view in layout["viewLayouts"]), layout["viewLayouts"]
    assert all(view["controlsFit"] for view in layout["viewLayouts"]), layout["viewLayouts"]


@pytest.mark.parametrize("width", [320, 375, 768])
def test_content_grids_stack_at_mobile_and_tablet_widths(width):
    """Catches desktop grid rules and inline styles overriding mobile stacking."""
    layout = _render_probe(width, "app")

    assert layout["analyticsColumns"] == 1
    assert all(columns == 1 for columns in layout["formColumns"])


@pytest.mark.parametrize("width", [320, 375, 780, 900])
def test_login_form_fits_phone_viewport(width):
    """Catches the fixed-width desktop login split-screen clipping on phones."""
    layout = _render_probe(width, "login")

    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert layout["gateDisplay"] in {"block", "flex"}
    assert layout["leftDisplay"] == "none"
    assert layout["rightWidth"] <= layout["viewportWidth"]


def test_mobile_drawer_manages_accessibility_and_keyboard_focus():
    """Catches focus entering an off-canvas drawer or becoming lost after Escape."""
    drawer = _render_probe(375, "drawer")

    assert drawer["opened"] == {
        "expanded": "true",
        "label": "Close menu",
        "hidden": "false",
        "inert": False,
        "focusedInside": True,
        "tabWrapsForward": True,
        "tabWrapsBackward": True,
    }
    assert drawer["closed"] == {
        "bodyOpen": False,
        "expanded": "false",
        "hidden": "true",
        "inert": True,
        "focusRestored": True,
    }
    assert drawer["navigationClose"] == {
        "bodyOpen": False,
        "drawerInert": True,
        "focusOutsideDrawer": True,
        "focusRestored": True,
    }
