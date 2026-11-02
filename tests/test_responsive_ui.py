"""Rendered-layout regression tests for the single-page web UI."""

from __future__ import annotations

import html
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
frame.addEventListener('load', async () => {{
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  let result;
  if ('{target}' === 'notification-channels') {{
    win.hideLogin();
    win.api = async path => path === '/notifications/configs'
      ? [{{id:1,channel:'email',enabled:true,config:{{recipients:['finance.operations@example.com','chief.finance@example.com']}}}}]
      : [{{id:1,kind:'digest',cadence:'weekly',enabled:true,config:{{top_n:5}},last_status:'error',last_error:'HTTP 429'}}];
    doc.querySelectorAll('main .wrap').forEach(el => el.style.display = el.id === 'v-notifications' ? 'block' : 'none');
    await win.loadNotifications();
    result = {{
      viewportWidth: doc.documentElement.clientWidth,
      scrollWidth: doc.documentElement.scrollWidth,
      bodies: [...doc.querySelectorAll('#nt-list .card>div,#sch-list .card>div')]
        .map(el => Math.round(el.getBoundingClientRect().width))
    }};
  }} else if ('{target}' === 'login') {{
    win.showLogin();
    const gate = doc.getElementById('login-gate');
    const left = doc.querySelector('.lg-left');
    const right = doc.querySelector('.lg-right');
    const leftRect = left.getBoundingClientRect();
    const rightRect = right.getBoundingClientRect();
    result = {{
      viewportWidth: doc.documentElement.clientWidth,
      scrollWidth: doc.documentElement.scrollWidth,
      gateDisplay: win.getComputedStyle(gate).display,
      leftDisplay: win.getComputedStyle(left).display,
      rightWidth: Math.ceil(rightRect.width),
      introAboveForm: leftRect.bottom <= rightRect.top + 1,
      introBesideForm: leftRect.right <= rightRect.left + 1,
      headlineVisible: doc.querySelector('.lg-left h2').getClientRects().length > 0,
      descriptionVisible: doc.querySelector('.lg-left p').getClientRects().length > 0
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
    const navButton = drawer.querySelector('[data-view="analytics"]');
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


@pytest.mark.parametrize("width", [320, 375, 768, 900])
def test_login_introduction_stacks_above_form_on_compact_screens(width):
    """Catches the product introduction disappearing or sitting beside a cramped form."""
    layout = _render_probe(width, "login")

    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert layout["gateDisplay"] in {"block", "flex"}
    assert layout["leftDisplay"] != "none"
    assert layout["headlineVisible"] is True
    assert layout["descriptionVisible"] is True
    assert layout["introAboveForm"] is True
    assert layout["rightWidth"] <= layout["viewportWidth"]


@pytest.mark.parametrize("width", [901, 1024, 1440, 1920])
def test_login_introduction_stays_beside_form_on_desktop(width):
    """Catches compact login stacking leaking into desktop split-screen layouts."""
    layout = _render_probe(width, "login")

    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert layout["leftDisplay"] == "flex"
    assert layout["headlineVisible"] is True
    assert layout["descriptionVisible"] is True
    assert layout["introBesideForm"] is True


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


def _import_probe_page(width, theme):
    """Exercise the real UI and API, including cleanup, inside a sized frame."""
    script = r"""
const frame=document.getElementById('site');
const wait=async test=>{for(let i=0;i<200;i++){if(test())return;await new Promise(r=>setTimeout(r,25));}throw Error('Timed out waiting for UI');};
frame.onload=async()=>{
  const w=frame.contentWindow,d=frame.contentDocument,$=id=>d.getElementById(id),check=(value,msg)=>{if(!value)throw Error(msg)};
  try{
    await wait(()=>w.eval('routerReady'));
    d.documentElement.setAttribute('data-theme',THEME);w.nav('import');
    const csv=await fetch('/sample').then(r=>r.text());
    const importFile=async()=>{await w.uploadFile(new w.File([csv],'sample_fpa.csv',{type:'text/csv'}));await w.confirmMapping();};
    await importFile();
    check($('st-3').getAttribute('aria-current')==='step','Current step missing');
    check($('st-1').textContent.includes('done')&&$('st-1').textContent.includes('✓'),'Completed step missing');
    const rows=[...d.querySelectorAll('.kpi-grid[data-src]')];check(rows.length>2,'Sample KPI rows missing');
    check(rows.every(r=>r.querySelector('.k-include').checked),'Include default');
    check(rows.every(r=>[...r.querySelectorAll('input,select')].every(el=>el.getAttribute('aria-label')?.includes(r.dataset.src))),
      'Row labels missing');
    check(rows.every(r=>r.querySelector('.kpi-source').textContent.includes(r.dataset.src)),'Source names missing');
    w.selectKpis(false);check($('imp-finish').disabled,'Empty selection allowed');
    rows.slice(0,2).forEach(r=>{r.querySelector('.k-include').click();r.querySelector('.k-select').click();});
    $('kpi-bulk-category').value='Operating costs';$('kpi-bulk-unit').value='count';$('kpi-bulk-direction').value='down';$('kpi-bulk-apply').click();
    check(rows[0].querySelector('.k-cat').value==='Operating costs'&&rows[1].querySelector('.k-dir').value==='down','Bulk edit failed');
    check(rows[2].querySelector('.k-cat').value!=='Operating costs','Bulk edit leaked');
    const layout={viewport:d.documentElement.clientWidth,scroll:d.documentElement.scrollWidth,
      columns:w.getComputedStyle(rows[0]).gridTemplateColumns.split(' ').length,
      fieldLabels:[...rows[0].querySelectorAll('.kpi-field-label')].every(el=>el.getClientRects().length),
      bulkTargetHeight:Math.min(...[...d.querySelectorAll('.kpi-bulk input,.kpi-bulk select')].map(el=>el.getBoundingClientRect().height)),
      theme:d.documentElement.getAttribute('data-theme')};
    $('imp-name').value='Sample finance';$('imp-finish').click();await wait(()=>/tab=kpis/.test(w.location.hash));
    const configured=await w.api('/kpis');check(configured.selected.length===2,'Excluded KPIs were sent');
    check(configured.selected.every(k=>k.category==='Operating costs'&&k.direction_good==='down'&&k.unit==='count'),'Bulk values not saved');
    w.nav('import');await importFile();$('imp-name').value='Sample finance';
    const pending=w.eval('lastImport.datasetId');
    await w.api('/datasets/'+pending,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Sample finance'})});
    await w.loadDatasets();$('imp-mode').value='join';await w.onImportModeChange();
    const options=[...$('imp-target').options];check(options.length===2,'Duplicate datasets missing');
    check(options.every(o=>/KPIs? · imported/.test(o.textContent)),'Target metadata missing');
    const choices=[...d.querySelectorAll('#ws-menu .ws-choice')];
    check(choices.every(el=>/KPIs? · imported/.test(el.textContent)),'Picker metadata missing');
    $('imp-mode').value='new';await w.onImportModeChange();
    const leave=()=>w.requestImportView('dashboard');
    let leaving=leave();await wait(()=>$('modal-back').classList.contains('open'));w.closeModal();await leaving;
    check(/tab=data|#data$/.test(w.location.hash)&&w.eval('lastImport.datasetId')===pending,'Cancel lost import');
    leaving=leave();await wait(()=>$('modal-back').classList.contains('open'));$('modal-ok').click();await leaving;
    check(/tab=kpis/.test(w.location.hash),'Leave did not navigate');
    const datasets=await w.api('/datasets');check(datasets.datasets.length===1,'Abandoned dataset not removed');
    document.getElementById('result').textContent=JSON.stringify({...layout,kpis:configured.selected.length,cleaned:true});
  }catch(e){document.getElementById('result').textContent=JSON.stringify({error:e.stack});}
};
""".replace("THEME", json.dumps(theme))
    return (f'<!doctype html><meta charset="utf-8"><iframe id="site" src="/app" '
            f'style="width:{width}px;height:1000px;border:0"></iframe><pre id="result">waiting</pre>'
            f'<script>{script}</script>').encode()


@pytest.mark.parametrize("width", [375, 1280])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sample_import_kpi_selection_and_abandonment_fit_viewport(width, theme, tmp_path, monkeypatch):
    """Catch import payload, accessible controls, phone layout and cleanup regressions."""
    from fastapi.testclient import TestClient

    from app import main
    from app.config import settings
    from app.deps import shared_cache

    browser = _chrome_path()
    if not browser:
        pytest.skip("Chrome is required for the import flow")
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "import.db"))
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "redis_url", "")
    shared_cache.cache_clear()
    main.init_db()

    with TestClient(main.app) as client:
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/probe":
                    return self.send_body(_import_probe_page(width, theme), "text/html; charset=utf-8")
                if self.path == "/sample":
                    return self.send_body((WEB_ROOT.parents[1] / "data/sample_fpa.csv").read_bytes(), "text/csv")
                self.proxy()

            def do_POST(self):
                self.proxy()

            do_PATCH = do_POST
            do_DELETE = do_POST

            def send_body(self, body, content_type, status=200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def proxy(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                response = client.request(self.command, self.path, content=body,
                                          headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
                self.send_body(response.content, response.headers.get("content-type", "application/json"), response.status_code)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            proc = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={tmp_path / 'chrome'}", "--virtual-time-budget=20000",
                                   "--window-size=1400,1200", "--dump-dom", f"http://127.0.0.1:{server.server_port}/probe"],
                                  capture_output=True, text=True, timeout=60, check=True)
        finally:
            server.shutdown()
            thread.join()
    shared_cache.cache_clear()
    payload = html.unescape(proc.stdout.split('<pre id="result">', 1)[1].split("</pre>", 1)[0])
    result = json.loads(payload)
    assert "error" not in result, result
    assert result["scroll"] <= result["viewport"]
    assert result["columns"] == (1 if width == 375 else 5)
    assert result["fieldLabels"] == (width == 375)
    assert result["bulkTargetHeight"] >= 36
    assert result["kpis"] == 2 and result["cleaned"]


@pytest.mark.parametrize("width", [375, 1280])
def test_real_notification_and_schedule_rows_leave_room_for_readable_details(width):
    layout = _render_probe(width, "notification-channels")
    assert layout["scrollWidth"] <= layout["viewportWidth"]
    assert len(layout["bodies"]) == 2
    assert all(body >= 200 for body in layout["bodies"]), layout
