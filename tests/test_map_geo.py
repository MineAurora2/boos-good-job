"""校验前端内置地级市地图数据的覆盖范围、可渲染性和体积约束。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CITY_GEO_PATH = ROOT / 'dashboard' / 'china-cities.json'


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
        """每个要素都应具有 ECharts 可渲染几何和标签定位点。"""
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


if __name__ == '__main__':
    unittest.main()
