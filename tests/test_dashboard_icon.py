from pathlib import Path
import struct
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.control import router as control_router
from app.security import HybridAuthMiddleware, SecurityPolicy, _PUBLIC_DASHBOARD_PATHS


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'dashboard'


class DashboardIconAssetTests(unittest.TestCase):
    def test_dashboard_ships_all_icon_formats(self) -> None:
        for name in (
            'favicon.svg',
            'favicon.ico',
            'favicon-32x32.png',
            'favicon-16x16.png',
        ):
            self.assertTrue((DASHBOARD / 'assets' / name).is_file(), name)

    def test_svg_uses_the_approved_trend_pulse_palette(self) -> None:
        svg_path = DASHBOARD / 'assets' / 'favicon.svg'
        self.assertTrue(svg_path.is_file(), 'favicon.svg')
        svg = svg_path.read_text(encoding='utf-8')
        self.assertIn('viewBox="0 0 64 64"', svg)
        for color in ('#0F2230', '#39D7F2', '#56D9A5', '#EEF8FB'):
            self.assertIn(color, svg)

    def test_png_and_ico_dimensions_match_the_declared_sizes(self) -> None:
        png_signature = b'\x89PNG\r\n\x1a\n'
        png_payloads = {}
        for size in (16, 32):
            payload = (DASHBOARD / 'assets' / f'favicon-{size}x{size}.png').read_bytes()
            self.assertEqual(payload[:8], png_signature)
            self.assertEqual(struct.unpack('>II', payload[16:24]), (size, size))
            png_payloads[size] = payload

        ico = (DASHBOARD / 'assets' / 'favicon.ico').read_bytes()
        self.assertEqual(struct.unpack_from('<HHH', ico), (0, 1, 2))
        for index, size in enumerate((16, 32)):
            width, height, colors, reserved, planes, bits, length, offset = (
                struct.unpack_from('<BBBBHHII', ico, 6 + (index * 16))
            )
            self.assertEqual((width, height, colors, reserved), (size, size, 0, 0))
            self.assertEqual((planes, bits), (1, 32))
            self.assertEqual(ico[offset:offset + length], png_payloads[size])

    def test_build_script_discovers_machine_and_per_user_edge_installations(self) -> None:
        script = (ROOT / 'scripts' / 'build_dashboard_icon.ps1').read_text(encoding='utf-8')
        self.assertIn('Get-Command', script)
        self.assertIn('LOCALAPPDATA', script)

    def test_dashboard_declares_favicon_fallbacks_and_sidebar_image(self) -> None:
        html = (DASHBOARD / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/dashboard/assets/favicon.svg', html)
        self.assertIn('/dashboard/assets/favicon.ico', html)
        self.assertIn('/dashboard/assets/favicon-32x32.png', html)
        self.assertIn('/dashboard/assets/favicon-16x16.png', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn('src="/dashboard/assets/favicon.svg"', html)


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

    def test_icon_routes_bypass_auth_only_for_read_requests(self) -> None:
        application = FastAPI()
        application.add_middleware(
            HybridAuthMiddleware,
            policy=SecurityPolicy.from_env({'GOODJOB_SHARED_TOKEN': 'x' * 32}),
        )
        application.include_router(control_router)

        with TestClient(
            application,
            base_url='https://backend.example',
            client=('203.0.113.10', 50000),
        ) as client:
            for path in sorted(_PUBLIC_DASHBOARD_PATHS):
                if not path.startswith('/dashboard/assets/favicon'):
                    continue
                with self.subTest(method='GET', path=path):
                    self.assertEqual(client.get(path).status_code, 200)
                with self.subTest(method='HEAD', path=path):
                    self.assertEqual(client.head(path).status_code, 200)
                with self.subTest(method='POST', path=path):
                    self.assertEqual(client.post(path).status_code, 401)


if __name__ == '__main__':
    unittest.main()
