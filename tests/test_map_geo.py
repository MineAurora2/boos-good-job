"""校验前端内置地级市地图数据的覆盖范围、可渲染性和体积约束。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CITY_GEO_PATH = ROOT / 'dashboard' / 'china-cities.json'
DASHBOARD_JS_PATH = ROOT / 'dashboard' / 'app.js'
DASHBOARD_HTML_PATH = ROOT / 'dashboard' / 'index.html'
DASHBOARD_CSS_PATH = ROOT / 'dashboard' / 'styles.css'


class CityGeoDataTests(unittest.TestCase):
    """防止地图构建脚本生成缺区、无中心点或体积过大的 GeoJSON。"""

    @classmethod
    def setUpClass(cls):
        """整套测试只解析一次较大的地图文件，缩短回归测试时间。"""
        cls.payload = json.loads(CITY_GEO_PATH.read_text(encoding='utf-8'))
        cls.features = cls.payload['features']

    def test_contains_nationwide_prefecture_boundaries(self):
        """全国地级要素数量应合理，名称唯一，重点地区必须存在。"""
        names = [feature['properties']['name'] for feature in self.features]

        self.assertGreaterEqual(len(names), 350)
        self.assertEqual(len(names), len(set(names)))
        for expected in ('北京市', '广州市', '深圳市', '香港特别行政区', '澳门特别行政区'):
            self.assertIn(expected, names)

    def test_every_feature_has_renderable_geometry_and_center(self):
        """每个要素都应具有 SVG 可渲染几何和标签定位点。"""
        for feature in self.features:
            with self.subTest(name=feature['properties']['name']):
                self.assertIn(feature['geometry']['type'], {'Polygon', 'MultiPolygon'})
                self.assertTrue(feature['geometry']['coordinates'])
                self.assertTrue(feature['properties'].get('centroid') or feature['properties'].get('center'))

    def test_bundled_map_stays_small_enough_for_lazy_loading(self):
        """限制静态资源体积，避免切换地级市模式时加载等待过长。"""
        self.assertLess(CITY_GEO_PATH.stat().st_size, 2 * 1024 * 1024)
        self.assertEqual(self.payload['type'], 'FeatureCollection')
        self.assertTrue(str(self.payload.get('source', '')).startswith('https://geo.datav.aliyun.com/'))

    def test_city_mode_uses_local_svg_without_online_map_runtime(self):
        """运行时不得加载在线地图 SDK 或瓦片，地级市始终由本地 SVG 数据绘制。"""
        frontend_source = '\n'.join(
            (
                DASHBOARD_JS_PATH.read_text(encoding='utf-8'),
                DASHBOARD_HTML_PATH.read_text(encoding='utf-8'),
                DASHBOARD_CSS_PATH.read_text(encoding='utf-8'),
            )
        ).lower()
        for forbidden in ('leaflet', 'openstreetmap', 'tianditu', 'mapbox', 'amap.'):
            self.assertNotIn(forbidden, frontend_source)

        self.assertIn('geo-city-label', frontend_source)
        self.assertIn('data-base-font-size', frontend_source)
        self.assertIn('const map_max_scale = 20', frontend_source)

    def test_zoom_automatically_switches_map_detail_with_hysteresis(self):
        """层级由缩放自动控制，且进入、退出阈值应留出回差防止临界抖动。"""
        script = DASHBOARD_JS_PATH.read_text(encoding='utf-8')
        html = DASHBOARD_HTML_PATH.read_text(encoding='utf-8')
        enter = float(re.search(r'CITY_DETAIL_ENTER_SCALE\s*=\s*([\d.]+)', script).group(1))
        exit_scale = float(re.search(r'CITY_DETAIL_EXIT_SCALE\s*=\s*([\d.]+)', script).group(1))

        self.assertGreater(exit_scale, 1)
        self.assertLess(exit_scale, enter)
        self.assertLessEqual(enter, 10)
        self.assertNotIn('mapLevelSwitch', script)
        self.assertNotIn('mapLevelSwitch', html)
        self.assertNotIn('data-map-level', html)
        self.assertNotIn("map.addEventListener('dblclick'", script)

        zoom_function = script.split('function zoomMap(', 1)[1].split('function resetMapView', 1)[0]
        self.assertIn('syncMapLevelWithScale()', zoom_function)
        self.assertIn('if (levelChanged) updateDashboard()', zoom_function)

    def test_city_mode_has_no_count_badges_or_native_svg_tooltips(self):
        """地级市只显示色阶和名称，地图路径不生成会弹白框的原生 SVG title。"""
        script = DASHBOARD_JS_PATH.read_text(encoding='utf-8')
        styles = DASHBOARD_CSS_PATH.read_text(encoding='utf-8')
        self.assertNotIn('city-count-marker', script)
        self.assertNotIn('city-count-marker', styles)
        self.assertIn('const hideCityCount', script)
        self.assertNotIn('<title>${title}', script)
        self.assertNotIn('<title>${name}热力区域', script)
        self.assertNotIn("state.mapCity === city ? 'is-active'", script)

    def test_map_interactions_have_no_white_box_visuals(self):
        """省市及港澳交互只使用主题色描边，不使用纯白边框、阴影或浏览器轮廓。"""
        styles = DASHBOARD_CSS_PATH.read_text(encoding='utf-8')
        map_rules = styles.split('.china-map .geo-province', 1)[1].split('.province-label.hot', 1)[0]

        self.assertNotIn('stroke: #fff', map_rules)
        self.assertNotIn('drop-shadow', map_rules)
        self.assertNotIn('box-shadow', map_rules)
        self.assertGreaterEqual(map_rules.count('outline: none'), 3)
        self.assertIn('filter: none', map_rules)


if __name__ == '__main__':
    unittest.main()
