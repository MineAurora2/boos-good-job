"""生成前端按需加载的全国地级市 GeoJSON 数据。

脚本从公开行政区划数据源下载各省地级边界，将坐标精度压缩并简化折线后写入
``dashboard/china-cities.json``。直辖市、台湾、香港和澳门直接复用省级地图要素。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = 'https://geo.datav.aliyun.com/areas_v3/bound/{adcode}_full.json'
PROVINCE_CODES = (
    130000, 140000, 150000, 210000, 220000, 230000,
    320000, 330000, 340000, 350000, 360000, 370000,
    410000, 420000, 430000, 440000, 450000, 460000,
    510000, 520000, 530000, 540000, 610000, 620000,
    630000, 640000, 650000,
)
DIRECT_CITY_CODES = {110000, 120000, 310000, 500000, 710000, 810000, 820000}


def _point_segment_distance_squared(point, start, end) -> float:
    """返回点到线段的平方距离，避免简化过程中反复计算平方根。"""
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    if not dx and not dy:
        return (px - x1) ** 2 + (py - y1) ** 2
    ratio = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    x, y = x1 + ratio * dx, y1 + ratio * dy
    return (px - x) ** 2 + (py - y) ** 2


def _simplify_open(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """用 Douglas-Peucker 算法递归简化一条非闭合折线。"""
    if len(points) <= 2:
        return points
    threshold = tolerance * tolerance
    start, end = points[0], points[-1]
    # 只保留偏离首尾基准线超过容差的最远点，再对其左右两段递归处理。
    farthest_index = -1
    farthest_distance = threshold
    for index, point in enumerate(points[1:-1], 1):
        distance = _point_segment_distance_squared(point, start, end)
        if distance > farthest_distance:
            farthest_index, farthest_distance = index, distance
    if farthest_index < 0:
        return [start, end]
    left = _simplify_open(points[:farthest_index + 1], tolerance)
    right = _simplify_open(points[farthest_index:], tolerance)
    return left[:-1] + right


def _deduplicate(points: list[list[float]]) -> list[list[float]]:
    """统一坐标精度并移除连续重复点，以缩小文件且稳定后续比较。"""
    result = []
    for longitude, latitude, *_ in points:
        point = [round(float(longitude), 4), round(float(latitude), 4)]
        if not result or point != result[-1]:
            result.append(point)
    return result


def _ring_arc(points: list[list[float]], start: int, end: int) -> list[list[float]]:
    """从环形点列截取一段弧，支持终点跨过数组尾部。"""
    if start <= end:
        return points[start:end + 1]
    return points[start:] + points[:end + 1]


def _simplify_ring(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """把闭合边界拆成两条开放弧简化，再恢复合法闭环。"""
    clean = _deduplicate(points)
    if len(clean) > 1 and clean[0] == clean[-1]:
        clean.pop()
    if len(clean) < 5:
        return clean + clean[:1]

    # 选择左右极值点作为两条弧的稳定端点，避免直接把首尾相同的闭环当成
    # 零长度折线，导致整个多边形被错误压缩。
    left = min(range(len(clean)), key=lambda index: (clean[index][0], clean[index][1]))
    right = max(range(len(clean)), key=lambda index: (clean[index][0], clean[index][1]))
    if left == right:
        bottom = min(range(len(clean)), key=lambda index: clean[index][1])
        top = max(range(len(clean)), key=lambda index: clean[index][1])
        left, right = bottom, top

    first = _simplify_open(_ring_arc(clean, left, right), tolerance)
    second = _simplify_open(_ring_arc(clean, right, left), tolerance)
    simplified = first[:-1] + second[:-1]
    if len(simplified) < 3:
        simplified = clean
    return simplified + simplified[:1]


def _simplify_geometry(geometry: dict, tolerance: float) -> dict:
    """保持 Polygon/MultiPolygon 层级不变，逐环简化行政区几何。"""
    geometry_type = geometry.get('type')
    coordinates = geometry.get('coordinates') or []
    polygons = [coordinates] if geometry_type == 'Polygon' else coordinates if geometry_type == 'MultiPolygon' else []
    simplified = [
        [_simplify_ring(ring, tolerance) for ring in polygon if len(ring) >= 4]
        for polygon in polygons
    ]
    simplified = [polygon for polygon in simplified if polygon]
    return {
        'type': 'Polygon' if geometry_type == 'Polygon' else 'MultiPolygon',
        'coordinates': simplified[0] if geometry_type == 'Polygon' and simplified else simplified,
    }


def _download(adcode: int) -> dict:
    """下载一个省级行政代码对应的地级边界集合。"""
    request = Request(
        SOURCE_URL.format(adcode=adcode),
        headers={'User-Agent': 'czc-good-job map-data builder'},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def _compact_feature(feature: dict, province: str, tolerance: float) -> dict:
    """仅保留渲染和标签定位需要的属性，并简化几何数据。"""
    properties = feature.get('properties') or {}
    return {
        'type': 'Feature',
        'properties': {
            'adcode': properties.get('adcode'),
            'name': properties.get('name'),
            'province': province,
            'center': properties.get('center'),
            'centroid': properties.get('centroid'),
        },
        'geometry': _simplify_geometry(feature.get('geometry') or {}, tolerance),
    }


def build(output: Path, tolerance: float) -> None:
    """构建完整地级市 FeatureCollection 并写入指定文件。"""
    province_geo = json.loads((ROOT / 'dashboard' / 'china.json').read_text(encoding='utf-8'))
    provinces = {
        int(feature['properties']['adcode']): feature
        for feature in province_geo.get('features', [])
        if str(feature.get('properties', {}).get('adcode', '')).isdigit()
    }
    features = []
    # 这些地区在省级底图中已经是一条可展示的独立边界，无需再次下载下级区县。
    for adcode in sorted(DIRECT_CITY_CODES):
        feature = provinces[adcode]
        province = feature['properties']['name']
        features.append(_compact_feature(feature, province, tolerance))

    for adcode in PROVINCE_CODES:
        province = provinces[adcode]['properties']['name']
        source = _download(adcode)
        for feature in source.get('features', []):
            if feature.get('properties', {}).get('name') and feature.get('geometry'):
                features.append(_compact_feature(feature, province, tolerance))

    payload = {
        'type': 'FeatureCollection',
        'source': 'https://geo.datav.aliyun.com/areas_v3/bound/',
        'simplifyTolerance': tolerance,
        'features': features,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'wrote {len(features)} city features to {output} ({output.stat().st_size} bytes)')


def main() -> None:
    """解析命令行参数；容差越大，输出越小，但行政边界细节越少。"""
    parser = argparse.ArgumentParser(description='Build the bundled prefecture-level China GeoJSON.')
    parser.add_argument('--output', type=Path, default=ROOT / 'dashboard' / 'china-cities.json')
    parser.add_argument('--tolerance', type=float, default=0.008)
    args = parser.parse_args()
    build(args.output.resolve(), max(0.0, args.tolerance))


if __name__ == '__main__':
    main()
