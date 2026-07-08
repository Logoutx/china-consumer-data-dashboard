// lib/city-groups.mjs — the 70-city group definitions, ported EXACTLY from
// the old root app.js (coreCityGroup / capitalAndNewTierCities / the
// "other" catch-all) per the owner's restore-depth instruction: "port its
// exact group definitions." Do not reorder or edit these lists — they are
// the site's prior editorial judgment about which cities are "北上广深",
// "省会和新一线", and everything else.

export const CORE_CITIES = ['北京', '上海', '广州', '深圳'];

export const CAPITAL_AND_NEW_TIER_CITIES = [
  '天津',
  '石家庄',
  '太原',
  '呼和浩特',
  '沈阳',
  '长春',
  '哈尔滨',
  '南京',
  '杭州',
  '宁波',
  '合肥',
  '福州',
  '南昌',
  '济南',
  '青岛',
  '郑州',
  '武汉',
  '长沙',
  '南宁',
  '海口',
  '重庆',
  '成都',
  '贵阳',
  '昆明',
  '西安',
  '兰州',
  '西宁',
  '银川',
  '乌鲁木齐',
];

/**
 * Group `cities` (whatever the panel actually has, which may be a subset of
 * all 70) into 北上广深 / 省会和新一线 / 其他城市, same logic as the old
 * app.js's propertyCityGroups(): filter each predefined list against what's
 * actually available, assign everything left over to "other".
 */
export function cityGroups(cities) {
  const available = new Set(cities);
  const core = CORE_CITIES.filter((c) => available.has(c));
  const capitalAndNewTier = CAPITAL_AND_NEW_TIER_CITIES.filter((c) => available.has(c) && !core.includes(c));
  const assigned = new Set([...core, ...capitalAndNewTier]);
  const other = cities.filter((c) => !assigned.has(c));
  return [
    { key: 'core', label: '北上广深', cities: core },
    { key: 'capital-new-tier', label: '省会和新一线', cities: capitalAndNewTier },
    { key: 'other', label: '其他城市', cities: other },
  ].filter((g) => g.cities.length);
}
