# Dashboard Icon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a unified “trend pulse” favicon and sidebar brand mark to the statistics dashboard.

**Architecture:** Keep `dashboard/assets/favicon.svg` as the visual source and use a small PowerShell build script to render 16 px and 32 px PNGs with installed Microsoft Edge, then package those PNGs into a standards-compliant ICO file. Serve each asset through explicit FastAPI routes, declare browser fallbacks in the dashboard head, and reuse the SVG directly in the sidebar.

**Tech Stack:** HTML, CSS, SVG, PowerShell, Microsoft Edge headless rendering, FastAPI, Python `unittest`

---

### Task 1: Add Failing Dashboard Icon Contract Tests

**Files:**
- Create: `tests/test_dashboard_icon.py`

- [ ] **Step 1: Write the failing asset and markup tests**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'dashboard'


class DashboardIconAssetTests(unittest.TestCase):
    def test_dashboard_ships_all_icon_formats(self) -> None:
        for name in ('favicon.svg', 'favicon.ico', 'favicon-32x32.png', 'favicon-16x16.png'):
            self.assertTrue((DASHBOARD / 'assets' / name).is_file(), name)

    def test_dashboard_declares_favicon_fallbacks_and_sidebar_image(self) -> None:
        html = (DASHBOARD / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/dashboard/assets/favicon.svg', html)
        self.assertIn('/dashboard/assets/favicon.ico', html)
        self.assertIn('/dashboard/assets/favicon-32x32.png', html)
        self.assertIn('/dashboard/assets/favicon-16x16.png', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn('src="/dashboard/assets/favicon.svg"', html)
```

- [ ] **Step 2: Write the failing route and security tests**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.control import router as control_router
from app.security import _PUBLIC_DASHBOARD_PATHS


class DashboardIconRouteTests(unittest.TestCase):
    def test_icon_assets_are_served_publicly_with_expected_media_types(self) -> None:
        application = FastAPI()
        application.include_router(control_router)
        client = TestClient(application)
        expected = {
            '/dashboard/assets/favicon.svg': 'image/svg+xml',
            '/dashboard/assets/favicon.ico': 'image/x-icon',
            '/dashboard/assets/favicon-32x32.png': 'image/png',
            '/dashboard/assets/favicon-16x16.png': 'image/png',
        }
        for path, media_type in expected.items():
            self.assertIn(path, _PUBLIC_DASHBOARD_PATHS)
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers['content-type'], media_type)
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_icon -v`

Expected: FAIL because `dashboard/assets/` and the new HTML declarations/routes do not exist.

### Task 2: Create the Reproducible Icon Assets

**Files:**
- Create: `dashboard/assets/favicon.svg`
- Create: `dashboard/assets/favicon-16x16.png`
- Create: `dashboard/assets/favicon-32x32.png`
- Create: `dashboard/assets/favicon.ico`
- Create: `scripts/build_dashboard_icon.ps1`

- [ ] **Step 1: Add the SVG source**

Create a 64 by 64 SVG containing a `#0F2230` rounded square, three ascending bars in `#39D7F2` and `#56D9A5`, and a `#EEF8FB` upward trend polyline with a compact arrowhead. Use only simple geometry so the mark survives 16 px rasterization.

- [ ] **Step 2: Add the deterministic build script**

The PowerShell script must locate Microsoft Edge, render the SVG at 16 and 32 pixels with `--headless --screenshot`, validate both PNG signatures, and write an ICO directory followed by the two complete PNG payloads. It exits non-zero when Edge or any generated file is missing.

- [ ] **Step 3: Generate raster assets**

Run: `powershell -ExecutionPolicy Bypass -File scripts/build_dashboard_icon.ps1`

Expected: the two PNGs and ICO exist under `dashboard/assets/`, and the command exits 0.

### Task 3: Serve and Integrate the Icon

**Files:**
- Modify: `app/routes/control.py`
- Modify: `app/security.py`
- Modify: `dashboard/index.html`
- Modify: `dashboard/styles.css`
- Modify: `.gitignore`

- [ ] **Step 1: Add explicit FastAPI asset routes**

Add four GET routes under `/dashboard/assets/`, each returning the matching file from `STATE.dashboard_dir / 'assets'` with the exact media type asserted by the test.

- [ ] **Step 2: Add asset paths to the public dashboard allowlist**

Add the same four paths to `_PUBLIC_DASHBOARD_PATHS` so non-admin browser requests can load page icons.

- [ ] **Step 3: Add favicon declarations and sidebar image**

Declare SVG, ICO, 32 px PNG, and 16 px PNG `<link rel="icon">` entries in `<head>`. Replace the three empty `<span>` elements inside the sidebar brand marker with:

```html
<img class="brand-mark" src="/dashboard/assets/favicon.svg" alt="" aria-hidden="true">
```

- [ ] **Step 4: Simplify the brand marker CSS**

Keep `.brand-mark` at a stable 38 by 38 pixels with `display: block`, `flex: 0 0 auto`, and the existing subtle glow. Remove the obsolete child span selectors.

- [ ] **Step 5: Ignore brainstorming session artifacts**

Add `.superpowers/` to `.gitignore` so the temporary visual companion files remain outside the implementation commit.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dashboard_icon -v`

Expected: all dashboard icon tests pass.

### Task 4: Full Verification and Local Commit

**Files:**
- Verify all files from Tasks 1-3

- [ ] **Step 1: Run the full repository checks**

Run: `powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1`

Expected: Python compilation, Python unit tests, JavaScript syntax checks, and Node contract tests all pass.

- [ ] **Step 2: Run browser QA**

Start the local server at `http://127.0.0.1:47999/dashboard`, verify the page title and meaningful dashboard DOM, confirm all four asset requests return 200, inspect console warnings/errors, capture desktop and mobile screenshots, and confirm the theme toggle changes the rendered theme without moving the brand mark.

- [ ] **Step 3: Stop project processes**

Stop the local dashboard server and verify no process started by this task remains running.

- [ ] **Step 4: Review diff and commit in Chinese**

Stage only the plan, tests, icon assets, build script, routes, security allowlist, HTML, CSS, and `.gitignore`, then commit with:

```text
功能：添加统计面板网站图标
```
