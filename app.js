const state = {
  section: "retail",
  version: "latest",
  mode: "value",
  range: "Max",
  retailFrequency: "period",
  incomeFrequency: "quarter",
  incomeScale: "percapita",
  propertyCity: "overall",
  datasets: {},
};

const colors = ["#248a3d", "#0066cc", "#b25a00", "#6e52c8", "#0f7b8f", "#b42318", "#5f6f52"];

const sections = {
  retail: {
    label: "社会消费品零售总额",
    eyebrow: "National Bureau of Statistics",
    dataUrl: "./retail_release_archive.json",
    sourceLabel: "国家统计局原文",
    sourceUrl: "https://www.stats.gov.cn/sj/zxfb/202605/t20260518_1963727.html",
    methodUrl: "https://www.stats.gov.cn/zs/tjws/zytjzbqs/shxfp/202410/t20241025_1957174.html",
    onlineMethodUrl: "https://www.stats.gov.cn/hd/cjwtjd/202302/t20230207_1902274.html",
    footnote:
      "数据来源：国家统计局“数据发布”归档、国家数据新版月度库。发布时数据来自发布稿原始披露；修正后数据来自国家数据新版接口及后续修订。网上商品零售额按同一年相邻累计值相减推导各月数值，缺少上月累计值时留空。TTM 为滚动 12 个可用报告期值；网上商品零售额占比的 TTM 使用滚动窗口内网上商品零售额合计 / 除汽车以外零售额合计计算。",
    preferred: [
      "retail_total",
      "retail_ex_auto",
      "auto_total",
      "urban",
      "rural",
      "online_goods",
      "online_ex_auto_share",
      "catering",
      "goods",
    ],
  },
  income: {
    label: "居民收入和支出",
    eyebrow: "National Bureau of Statistics",
    dataUrl: "./income_release_archive.json",
    sourceLabel: "国家统计局原文",
    sourceUrl: "https://www.stats.gov.cn/sj/zxfb/202604/t20260416_1963323.html",
    methodUrl: "https://www.stats.gov.cn/zs/tjws/zytjzbqs/jmrj/202411/t20241128_1957607.html",
    footnote:
      "数据来源：国家统计局居民收入和消费支出发布稿、住户收支与生活状况调查。季度模式由官方累计报告期值相邻相减得到单季度值；TTM 为最近四个单季度值滚动合计；年度模式取全年值。全国口径由人均值乘以国家统计局公布的对应年末人口换算为亿元；中位数不能汇总为全国总量，切换到全国口径时留空。2013-2016年为历史补充层，非完整发布稿口径，仅补入官方页面可核验的指标，缺项留空；其中2013-2015只有年度补充点，所以只在“年度”模式显示。",
    preferred: [
      "income_disposable",
      "income_disposable_urban",
      "income_disposable_rural",
      "income_wage",
      "income_business",
      "income_property",
      "income_transfer",
      "income_median",
      "income_median_urban",
      "income_median_rural",
      "consumption_expenditure",
      "consumption_expenditure_urban",
      "consumption_expenditure_rural",
      "consumption_food_tobacco_alcohol",
      "consumption_clothing",
      "consumption_housing",
      "consumption_household_services",
      "consumption_transport_communication",
      "consumption_education_culture",
      "consumption_healthcare",
      "consumption_other",
    ],
  },
  propertyPrice: {
    label: "70 城房价",
    eyebrow: "NBS / PBOC / MOF",
    dataUrl: "./property_release_archive.json",
    sourceLabel: "官方来源",
    sourceUrl: "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
    methodUrl: "https://www.stats.gov.cn/sj/zxfb/202606/t20260616_1963946.html",
    footnote:
      "数据来源：国家统计局《70个大中城市商品住宅销售价格变动情况》、国家数据“主要城市月度价格”。70城整体房价为70个城市指数的简单平均，环比/同比由指数减100得到；城市明细使用国家数据历史层和发布稿缓存，当前可追溯至2011-01。",
    preferred: [
      "new_home_70_price",
      "resale_home_70_price",
      "new_home_up_cities",
      "resale_home_up_cities",
    ],
  },
  propertyCredit: {
    label: "房贷和土地出让",
    eyebrow: "PBOC / MOF",
    dataUrl: "./property_release_archive.json",
    sourceLabel: "官方来源",
    sourceUrl: "https://www.pbc.gov.cn/",
    methodUrl: "https://www.pbc.gov.cn/",
    footnote:
      "数据来源：中国人民银行《金融机构贷款投向统计报告》、财政部《财政收支情况》。房贷余额为季度期末余额；地产税收和土地出让收入为财政部官方报告期值，历史页面存在当月、累计、年度等披露差异，图表保留官方报告期口径。",
    preferred: [
      "real_estate_loan_balance",
      "mortgage_balance",
      "property_development_loan_balance",
      "land_transfer_revenue",
      "real_estate_tax_total",
      "deed_tax",
      "property_tax",
      "urban_land_use_tax",
      "land_vat",
      "farmland_occupation_tax",
    ],
  },
};

const fmtValue = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
const fmtWhole = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const fmtPct = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

const populationWan = {
  2017: { total: 140011, urban: 84343, rural: 55668 },
  2018: { total: 140541, urban: 86433, rural: 54108 },
  2019: { total: 141008, urban: 88426, rural: 52582 },
  2020: { total: 141212, urban: 90220, rural: 50992 },
  2021: { total: 141260, urban: 91425, rural: 49835 },
  2022: { total: 141175, urban: 92071, rural: 49104 },
  2023: { total: 140967, urban: 93267, rural: 47700 },
  2024: { total: 140828, urban: 94350, rural: 46478 },
  2025: { total: 140489, urban: 95380, rural: 45109 },
};

function byId(id) {
  return document.getElementById(id);
}

function formatMixedSpacing(value) {
  const token = "[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*(?:[%％])?";
  return String(value)
    .replace(new RegExp(`([\\p{Script=Han}])\\s*(${token})`, "gu"), "$1 $2")
    .replace(new RegExp(`(${token})\\s*([\\p{Script=Han}])`, "gu"), "$1 $2");
}

function applyTypography(root = document.body) {
  const skipTags = new Set(["SCRIPT", "STYLE", "TEXTAREA"]);
  const walk = (node) => {
    if (node.nodeType === 3) {
      const next = formatMixedSpacing(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
      return;
    }
    if (node.nodeType !== 1 || skipTags.has(node.tagName)) return;
    node.childNodes.forEach(walk);
  };
  walk(root);
}

function currentSection() {
  return sections[state.section];
}

function payload() {
  return state.datasets[state.section];
}

function parsePeriod(period) {
  const [year, month] = period.split("-").map(Number);
  return new Date(year, month - 1, 1);
}

function monthDiff(a, b) {
  return (b.getFullYear() - a.getFullYear()) * 12 + b.getMonth() - a.getMonth();
}

const propertyCitySeries = {
  new_home_70_price: { cityMetric: "new_home_price", label: "新房价格" },
  resale_home_70_price: { cityMetric: "resale_home_price", label: "二手房价格" },
};

const propertyHomePriceCombinedId = "home_price_combined";
const propertyHomePriceBaseIds = ["new_home_70_price", "resale_home_70_price"];
const propertyOverallValue = "overall";
const propertyCoreValue = "core";
const propertyCapitalNewTierValue = "capitalNewTier";
const propertyOtherValue = "other";
const citySeriesSeparator = "__city__";

const coreCityGroup = ["北京", "上海", "广州", "深圳"];

const capitalAndNewTierCities = [
  "天津",
  "石家庄",
  "太原",
  "呼和浩特",
  "沈阳",
  "长春",
  "哈尔滨",
  "南京",
  "杭州",
  "宁波",
  "合肥",
  "福州",
  "南昌",
  "济南",
  "青岛",
  "郑州",
  "武汉",
  "长沙",
  "南宁",
  "海口",
  "重庆",
  "成都",
  "贵阳",
  "昆明",
  "西安",
  "兰州",
  "西宁",
  "银川",
  "乌鲁木齐",
];

function isPropertySection() {
  return state.section === "propertyPrice" || state.section === "propertyCredit";
}

function isPropertyPriceSection() {
  return state.section === "propertyPrice";
}

function baseSeriesId(seriesId) {
  return String(seriesId).split(citySeriesSeparator)[0];
}

function cityFromSeriesId(seriesId) {
  return String(seriesId).includes(citySeriesSeparator) ? String(seriesId).split(citySeriesSeparator)[1] : null;
}

function citySeriesId(seriesId, city) {
  return `${seriesId}${citySeriesSeparator}${city}`;
}

function isCombinedHomePriceSeries(seriesId) {
  return baseSeriesId(seriesId) === propertyHomePriceCombinedId;
}

function combinedHomePriceSeriesId(city) {
  return citySeriesId(propertyHomePriceCombinedId, city);
}

function seriesMeta(seriesId) {
  const data = payload();
  const baseId = baseSeriesId(seriesId);
  if (isCombinedHomePriceSeries(seriesId)) {
    const reference = data.series.new_home_70_price || {};
    const city = cityFromSeriesId(seriesId);
    return {
      ...reference,
      name: city ? `${city}房价` : "70 城平均房价",
      group: city ? "城市明细" : "70 城房价",
    };
  }
  return data.series[baseId] || {};
}

function seriesUnit(seriesId) {
  return seriesMeta(seriesId)?.unit;
}

function selectedPropertyGroup() {
  if (!isPropertyPriceSection()) return null;
  const cities = propertyCityNames();
  return propertyCityGroups(cities).find((group) => group.value === state.propertyCity) || null;
}

function displayUnit(seriesId) {
  if (state.section === "income" && state.incomeScale === "national" && seriesUnit(baseSeriesId(seriesId)) === "元") {
    return "亿元";
  }
  return seriesUnit(baseSeriesId(seriesId));
}

function effectiveMode(seriesId) {
  const meta = seriesMeta(seriesId);
  if (isPropertySection() && state.mode === "yoy" && !meta?.yoyLabel) return "value";
  return state.mode;
}

function formatMetric(seriesId, value, mode) {
  if (value == null) return "--";
  const unit = displayUnit(seriesId);
  if (seriesId === "online_ex_auto_share") {
    return mode === "value" ? `${fmtPct.format(value)}%` : `${value > 0 ? "+" : ""}${fmtPct.format(value)} pp`;
  }
  if (mode === "yoy") return `${value > 0 ? "+" : ""}${fmtPct.format(value)}%`;
  if (unit === "元") return `${fmtWhole.format(value)} 元`;
  if (unit === "个") return `${fmtWhole.format(value)} 个`;
  if (unit === "亿元" && Math.abs(value) >= 100000) return `${fmtValue.format(value / 10000)} 万亿`;
  if (unit === "亿元") return `${fmtValue.format(value)} 亿`;
  if (unit === "%") return `${fmtPct.format(value)}%`;
  return `${fmtValue.format(value)} 亿`;
}

function formatPeriod(period) {
  const [year, month] = period.split("-");
  return `${year}年${Number(month)}月`;
}

function displayPeriod(point) {
  return point?.label || (point?.period ? formatPeriod(point.period) : "无可用数据");
}

function sourceLink(seriesId) {
  const section = currentSection();
  const meta = seriesMeta(seriesId);
  if (meta.methodUrl) return meta.methodUrl;
  if (state.section === "retail" && (seriesId === "online_goods" || seriesId === "online_ex_auto_share")) {
    return section.onlineMethodUrl;
  }
  return section.methodUrl;
}

function sourceMeta(seriesId) {
  const meta = seriesMeta(seriesId);
  return {
    name: meta.source_name || "中国国家统计局",
    url: sourceLink(seriesId),
    label: meta.source_link_label || "统计方法说明",
  };
}

function metricValueForMode(metric, seriesId, mode) {
  if (!metric) return null;
  const suffix = mode === "value" ? "value" : "yoy";
  if (state.version === "published") return metric[`published_month_${suffix}`] ?? null;
  return metric[`latest_month_${suffix}`] ?? metric[`month_${suffix}`] ?? null;
}

function metricValue(metric, seriesId) {
  return metricValueForMode(metric, seriesId, effectiveMode(seriesId));
}

function recordMetric(record, seriesId) {
  const baseId = baseSeriesId(seriesId);
  const city = cityFromSeriesId(seriesId);
  const citySeries = propertyCitySeries[baseId];
  if (city && citySeries) {
    return record.cities?.[city]?.[citySeries.cityMetric] || null;
  }
  return record.metrics[baseId];
}

function absoluteMetricValue(metric) {
  if (!metric) return null;
  if (state.version === "published") return metric.published_month_value ?? null;
  return metric.latest_month_value ?? metric.month_value ?? null;
}

function recordLabel(record) {
  if (!record.period_label) return formatPeriod(record.period);
  return `${record.year}年${record.period_label}`;
}

function availablePoint(record, seriesId) {
  const value = metricValue(recordMetric(record, seriesId), seriesId);
  return value == null
    ? null
    : {
        date: parsePeriod(record.period),
        period: record.period,
        label: recordLabel(record),
        value,
      };
}

function reportPoint(record, seriesId, mode = effectiveMode(seriesId)) {
  const value = metricValueForMode(recordMetric(record, seriesId), seriesId, mode);
  return value == null
    ? null
    : {
        date: parsePeriod(record.period),
        period: record.period,
        label: recordLabel(record),
        value,
      };
}

function quarterName(month) {
  return { 3: "一季度", 6: "二季度", 9: "三季度", 12: "四季度" }[month] || `${month}月`;
}

function annualPopulation(year) {
  if (populationWan[year]) return populationWan[year];
  const years = Object.keys(populationWan)
    .map(Number)
    .filter((item) => item <= year)
    .sort((a, b) => a - b);
  return populationWan[years[years.length - 1]];
}

function populationKey(seriesId) {
  if (seriesId.includes("_median")) return null;
  if (seriesId.endsWith("_urban")) return "urban";
  if (seriesId.endsWith("_rural")) return "rural";
  return "total";
}

function scaleIncomeValue(seriesId, value, year) {
  if (value == null || state.incomeScale !== "national" || seriesUnit(seriesId) !== "元") return value;
  const key = populationKey(seriesId);
  const pop = key ? annualPopulation(year)?.[key] : null;
  return pop ? (value * pop) / 10000 : null;
}

function rollingWindowPoints(points, windowSize, seriesId) {
  return points
    .map((point, index) => {
      if (index < windowSize - 1) return null;
      const window = points.slice(index - windowSize + 1, index + 1);
      if (window.some((item) => item.value == null)) return null;
      const value = window.reduce((sum, item) => sum + item.value, 0);
      return {
        ...point,
        label: `${point.label} TTM`,
        value: Number(value.toFixed(seriesUnit(seriesId) === "元" ? 1 : 2)),
      };
    })
    .filter(Boolean);
}

function retailReportPoints(seriesId, mode = effectiveMode(seriesId)) {
  return payload()
    .records.map((record) => reportPoint(record, seriesId, mode))
    .filter(Boolean)
    .sort((a, b) => a.date - b.date);
}

function retailTtmSharePoints() {
  const online = new Map(rollingWindowPoints(retailReportPoints("online_goods", "value"), 12, "online_goods").map((point) => [point.period, point]));
  const exAuto = new Map(
    rollingWindowPoints(retailReportPoints("retail_ex_auto", "value"), 12, "retail_ex_auto").map((point) => [
      point.period,
      point,
    ]),
  );
  return Array.from(online.values())
    .map((point) => {
      const denominator = exAuto.get(point.period)?.value;
      if (!denominator) return null;
      return {
        ...point,
        value: Number(((point.value / denominator) * 100).toFixed(2)),
      };
    })
    .filter(Boolean);
}

function retailTtmPoints(seriesId) {
  if (seriesId === "online_ex_auto_share") return retailTtmSharePoints();
  return rollingWindowPoints(retailReportPoints(seriesId, "value"), 12, seriesId);
}

function incomeAbsolutePoints(seriesId) {
  const records = payload().records;
  const byPeriod = new Map(records.map((record) => [record.period, record]));
  const frequency = state.incomeFrequency === "ttm" ? "quarter" : state.incomeFrequency;
  return records
    .map((record) => {
      if (frequency === "annual" && record.month !== 12) return null;
      const metric = record.metrics[seriesId];
      const cumulative = absoluteMetricValue(metric);
      if (cumulative == null) return null;
      let value = cumulative;
      let label = record.month === 12 ? `${record.year}年` : `${record.year}年${record.period_label}`;

      if (frequency === "quarter") {
        const previous = record.month > 3 ? byPeriod.get(`${record.year}-${record.month - 3}`)?.metrics?.[seriesId] : null;
        if (record.month > 3 && !previous) return null;
        const previousValue = previous ? absoluteMetricValue(previous) : 0;
        if (previousValue == null) return null;
        value = cumulative - previousValue;
        label = `${record.year}年${quarterName(record.month)}`;
      }

      value = scaleIncomeValue(seriesId, value, record.year);
      return value == null
        ? null
        : {
            date: parsePeriod(record.period),
            period: record.period,
            label,
            supplement: Boolean(record.historical_supplement),
            value: Number(value.toFixed(1)),
          };
    })
    .filter(Boolean)
    .sort((a, b) => a.date - b.date);
}

function computedYoyPoints(points, difference = false) {
  const byKey = new Map(points.map((point) => [point.period, point.value]));
  return points
    .map((point) => {
      const [year, month] = point.period.split("-").map(Number);
      const previous = byKey.get(`${year - 1}-${String(month).padStart(2, "0")}`);
      if (point.value == null || previous == null || (!difference && previous === 0)) return null;
      return {
        ...point,
        value: Number((difference ? point.value - previous : (point.value / previous - 1) * 100).toFixed(2)),
      };
    })
    .filter(Boolean);
}

function filterRange(points) {
  if (!points.length) return points;
  const last = points[points.length - 1].date;
  if (state.range === "Max") return points;
  const months = Number.parseInt(state.range, 10) * 12;
  return points.filter((point) => monthDiff(point.date, last) <= months);
}

function chartSeries(seriesId) {
  if (isCombinedHomePriceSeries(seriesId)) return [];
  if (state.section === "income") {
    let points = incomeAbsolutePoints(seriesId);
    if (state.incomeFrequency === "ttm") points = rollingWindowPoints(points, 4, seriesId);
    return filterRange(state.mode === "yoy" ? computedYoyPoints(points) : points);
  }
  if (state.section === "retail" && state.retailFrequency === "ttm") {
    const points = retailTtmPoints(seriesId);
    return filterRange(state.mode === "yoy" ? computedYoyPoints(points, seriesId === "online_ex_auto_share") : points);
  }
  return filterRange(
    payload()
      .records.map((record) => availablePoint(record, seriesId))
      .filter(Boolean)
      .sort((a, b) => a.date - b.date),
  );
}

function combinedHomePriceLines(seriesId, index) {
  const city = cityFromSeriesId(seriesId);
  return propertyHomePriceBaseIds.map((baseId, offset) => {
    const id = city ? citySeriesId(baseId, city) : baseId;
    return {
      id,
      label: propertyCitySeries[baseId].label.replace("价格", ""),
      color: colors[(index + offset) % colors.length],
      points: chartSeries(id),
    };
  });
}

function displaySeriesName(seriesId, meta) {
  if (isCombinedHomePriceSeries(seriesId)) return meta.name;
  const city = cityFromSeriesId(seriesId);
  const citySeries = propertyCitySeries[baseSeriesId(seriesId)];
  return city && citySeries ? `${city}${citySeries.label}` : meta.name;
}

function allSeriesIds() {
  const data = payload();
  const ids = Object.keys(data.series);
  const preferred = currentSection().preferred;
  const group = selectedPropertyGroup();
  if (isPropertyPriceSection() && group?.value === propertyOverallValue) {
    return [
      propertyHomePriceCombinedId,
      ...preferred.filter((id) => ids.includes(id) && !propertyCitySeries[id]),
    ];
  }
  if (isPropertyPriceSection() && group?.value !== propertyOverallValue) {
    return group.cities.map(combinedHomePriceSeriesId);
  }
  if (isPropertySection()) {
    return preferred.filter((id) => ids.includes(id));
  }
  return [
    ...preferred.filter((id) => ids.includes(id)),
    ...ids.filter((id) => !preferred.includes(id)).sort((a, b) => {
      const ga = data.series[a].level ?? 99;
      const gb = data.series[b].level ?? 99;
      return ga - gb || data.series[a].name.localeCompare(data.series[b].name, "zh-CN");
    }),
  ];
}

function latestRecord() {
  const records = payload().records;
  return records[records.length - 1];
}

function updateHeadline() {
  const latest = latestRecord();
  const section = currentSection();
  document.body.classList.toggle("retail-section", state.section === "retail");
  document.body.classList.toggle("income-section", state.section === "income");
  document.body.classList.toggle("property-section", isPropertySection());
  document.body.classList.toggle("property-price-section", isPropertyPriceSection());
  byId("latestPeriod").textContent = latest ? recordLabel(latest) : "--";
  byId("eyebrow").textContent = section.eyebrow || "Official data";
  byId("sourceLink").textContent = section.sourceLabel;
  byId("sourceLink").href = latest?.url || section.sourceUrl;
  byId("footnoteText").textContent = section.footnote;
  populatePropertyCities();
}

function propertyCityNames() {
  const data = payload();
  if (!data?.cities) return [];
  return Array.from(new Set(Array.isArray(data.cities) ? data.cities : Object.keys(data.cities)));
}

function propertyCityGroups(cities) {
  const available = new Set(cities);
  const core = coreCityGroup.filter((city) => available.has(city));
  const capitalAndNewTier = capitalAndNewTierCities.filter((city) => available.has(city) && !core.includes(city));
  const assigned = new Set([...core, ...capitalAndNewTier]);
  const other = cities.filter((city) => !assigned.has(city));
  return [
    { value: propertyOverallValue, label: "70 城整体", cities },
    { value: propertyCoreValue, label: "北上广深", cities: core },
    { value: propertyCapitalNewTierValue, label: "省会和新一线", cities: capitalAndNewTier },
    { value: propertyOtherValue, label: "其他城市", cities: other },
  ].filter((group) => group.cities.length);
}

function makeCityButton(group) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.city = group.value;
  button.textContent = group.label;
  return button;
}

function populatePropertyCities() {
  const panel = byId("propertyCityButtons");
  if (!panel || !isPropertyPriceSection()) return;
  const cities = propertyCityNames();
  if (!cities.length) return;
  const groups = propertyCityGroups(cities);
  if (!groups.some((group) => group.value === state.propertyCity)) state.propertyCity = propertyOverallValue;
  const cityOptions = groups.map((group) => group.value);
  const currentOptions = Array.from(panel.querySelectorAll("button"))
    .map((button) => button.dataset.city)
    .join("|");
  const nextOptions = cityOptions.join("|");
  if (currentOptions !== nextOptions) {
    panel.replaceChildren(...groups.map(makeCityButton));
  }
  panel.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.city === state.propertyCity);
    button.setAttribute("aria-pressed", button.dataset.city === state.propertyCity ? "true" : "false");
  });
}

function makePath(points, xScale, yScale) {
  return points
    .map((point, index) => `${index ? "L" : "M"}${xScale(point.date).toFixed(1)} ${yScale(point.value).toFixed(1)}`)
    .join(" ");
}

function niceTicks(min, max, count = 4) {
  if (min === max) {
    const pad = Math.abs(min || 1) * 0.1;
    min -= pad;
    max += pad;
  }
  const span = max - min;
  const step = span / count;
  return Array.from({ length: count + 1 }, (_, index) => min + step * index);
}

function isPercentageAxis(seriesId) {
  const unit = displayUnit(seriesId);
  const mode = effectiveMode(seriesId);
  return mode === "yoy" || seriesId === "online_ex_auto_share" || unit === "%";
}

function ticksWithZero(min, max, count = 4) {
  const lower = Math.min(min, 0);
  const upper = Math.max(max, 0);
  const ticks = niceTicks(lower, upper, count);
  if (ticks.some((tick) => Math.abs(tick) < 1e-9)) return ticks;
  let nearestIndex = 0;
  ticks.forEach((tick, index) => {
    if (Math.abs(tick) < Math.abs(ticks[nearestIndex])) nearestIndex = index;
  });
  ticks[nearestIndex] = 0;
  return ticks.sort((a, b) => a - b);
}

function formatAxis(value, seriesId) {
  const unit = displayUnit(seriesId);
  if (isPercentageAxis(seriesId)) return fmtPct.format(value);
  if (unit === "个") return fmtWhole.format(value);
  if (Math.abs(value) >= 10000) return `${fmtValue.format(value / 10000)}万`;
  return fmtValue.format(value);
}

function drawChart(container, points, color, seriesId) {
  const width = 760;
  const height = container.classList.contains("primary-chart") ? 300 : 238;
  const pad = { left: 56, right: 16, top: 14, bottom: 34 };
  const values = points.map((point) => point.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  const extra = (max - min || Math.abs(max) || 1) * 0.12;
  const mode = effectiveMode(seriesId);
  const positiveAbsolute = mode === "value" && min >= 0;
  min = positiveAbsolute ? Math.max(0, min - extra) : min - extra;
  max += extra;
  if (isPercentageAxis(seriesId)) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }

  const first = points[0].date;
  const last = points[points.length - 1].date;
  const xScale = (date) => {
    const denom = last - first || 1;
    return pad.left + ((date - first) / denom) * (width - pad.left - pad.right);
  };
  const yScale = (value) => pad.top + ((max - value) / (max - min)) * (height - pad.top - pad.bottom);
  const ticks = isPercentageAxis(seriesId) ? ticksWithZero(min, max, 4) : niceTicks(min, max, 4);
  const path = makePath(points, xScale, yScale);
  const area = `${path} L${xScale(last).toFixed(1)} ${height - pad.bottom} L${xScale(first).toFixed(1)} ${height - pad.bottom} Z`;
  const years = Array.from(new Set(points.map((point) => point.date.getFullYear()))).filter((year, index, arr) => {
    if (arr.length <= 5) return true;
    return index === 0 || index === arr.length - 1 || index % Math.ceil(arr.length / 4) === 0;
  });

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${seriesMeta(seriesId).name}">
      <g class="axis">
        ${ticks
          .map((tick) => {
            const y = yScale(tick);
            return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"></line>
              <text x="4" y="${y + 4}">${formatAxis(tick, seriesId)}</text>`;
          })
          .join("")}
        ${years
          .map((year) => {
            const d = new Date(year, 0, 1);
            const x = Math.max(pad.left, Math.min(width - pad.right, xScale(d)));
            return `<text x="${x}" y="${height - 8}" text-anchor="middle">${year}</text>`;
          })
          .join("")}
      </g>
      <path class="area" d="${area}" fill="${color}"></path>
      <path class="line" d="${path}" stroke="${color}"></path>
      <circle cx="${xScale(last)}" cy="${yScale(points[points.length - 1].value)}" r="5" fill="${color}"></circle>
      <circle class="focus-point" data-focus cx="${xScale(last)}" cy="${yScale(points[points.length - 1].value)}" r="6" fill="${color}" stroke="white" stroke-width="2"></circle>
      ${points
        .map((point, index) => {
          const x = xScale(point.date).toFixed(1);
          const y = yScale(point.value).toFixed(1);
          const label = formatMetric(seriesId, point.value, mode);
          return `<circle class="hit-point" r="13" cx="${x}" cy="${y}" data-index="${index}" data-x="${x}" data-y="${y}" data-color="${color}" data-period="${point.period}" data-label="${point.label}" data-value="${label}" data-supplement="${point.supplement ? "1" : ""}"></circle>`;
        })
        .join("")}
    </svg>`;
  attachTooltip(container);
}

function drawCombinedChart(container, lines, seriesId) {
  const width = 760;
  const height = 238;
  const pad = { left: 56, right: 16, top: 22, bottom: 34 };
  const usableLines = lines.filter((line) => line.points.length > 1);
  const values = usableLines.flatMap((line) => line.points.map((point) => point.value));
  let min = Math.min(...values);
  let max = Math.max(...values);
  const extra = (max - min || Math.abs(max) || 1) * 0.12;
  const mode = effectiveMode(seriesId);
  min = mode === "value" && min >= 0 ? Math.max(0, min - extra) : min - extra;
  max += extra;
  if (isPercentageAxis(seriesId)) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }

  const dates = usableLines.flatMap((line) => line.points.map((point) => point.date));
  const first = new Date(Math.min(...dates));
  const last = new Date(Math.max(...dates));
  const xScale = (date) => {
    const denom = last - first || 1;
    return pad.left + ((date - first) / denom) * (width - pad.left - pad.right);
  };
  const yScale = (value) => pad.top + ((max - value) / (max - min)) * (height - pad.top - pad.bottom);
  const ticks = isPercentageAxis(seriesId) ? ticksWithZero(min, max, 4) : niceTicks(min, max, 4);
  const years = Array.from(new Set(dates.map((date) => date.getFullYear()))).filter((year, index, arr) => {
    if (arr.length <= 5) return true;
    return index === 0 || index === arr.length - 1 || index % Math.ceil(arr.length / 4) === 0;
  });

  container.innerHTML = `
    <div class="chart-legend">
      ${usableLines
        .map((line) => `<span><i style="background:${line.color}"></i>${line.label}</span>`)
        .join("")}
    </div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${seriesMeta(seriesId).name}">
      <g class="axis">
        ${ticks
          .map((tick) => {
            const y = yScale(tick);
            return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}"></line>
              <text x="4" y="${y + 4}">${formatAxis(tick, seriesId)}</text>`;
          })
          .join("")}
        ${years
          .map((year) => {
            const d = new Date(year, 0, 1);
            const x = Math.max(pad.left, Math.min(width - pad.right, xScale(d)));
            return `<text x="${x}" y="${height - 8}" text-anchor="middle">${year}</text>`;
          })
          .join("")}
      </g>
      ${usableLines
        .map((line, lineIndex) => {
          const path = makePath(line.points, xScale, yScale);
          const lineFirst = line.points[0].date;
          const lineLast = line.points[line.points.length - 1].date;
          const area = `${path} L${xScale(lineLast).toFixed(1)} ${height - pad.bottom} L${xScale(lineFirst).toFixed(1)} ${height - pad.bottom} Z`;
          return `<path class="area ${lineIndex ? "secondary" : ""}" d="${area}" fill="${line.color}"></path>
            <path class="line" d="${path}" stroke="${line.color}"></path>
            <circle cx="${xScale(lineLast)}" cy="${yScale(line.points[line.points.length - 1].value)}" r="4.5" fill="${line.color}"></circle>`;
        })
        .join("")}
      <circle class="focus-point" data-focus cx="${xScale(last)}" cy="${yScale(usableLines[0].points[usableLines[0].points.length - 1].value)}" r="6" fill="${usableLines[0].color}" stroke="white" stroke-width="2"></circle>
      ${usableLines
        .flatMap((line) =>
          line.points.map((point, index) => {
            const x = xScale(point.date).toFixed(1);
            const y = yScale(point.value).toFixed(1);
            const label = `${line.label} ${formatMetric(line.id, point.value, effectiveMode(line.id))}`;
            return `<circle class="hit-point" r="13" cx="${x}" cy="${y}" data-index="${index}" data-x="${x}" data-y="${y}" data-color="${line.color}" data-period="${point.period}" data-label="${point.label}" data-value="${label}" data-supplement="${point.supplement ? "1" : ""}"></circle>`;
          }),
        )
        .join("")}
    </svg>`;
  attachTooltip(container);
}

function makeCard(seriesId, index) {
  const meta = seriesMeta(seriesId);
  const points = chartSeries(seriesId);
  const combinedLines = isCombinedHomePriceSeries(seriesId) ? combinedHomePriceLines(seriesId, index) : [];
  const combinedUsable = combinedLines.filter((line) => line.points.length > 1);
  const latest = points[points.length - 1];
  const card = document.createElement("article");
  const isPrimary = seriesId === "retail_total" || seriesId === "income_disposable";
  card.className = `card ${isPrimary ? "primary" : ""}`;
  const color = colors[index % colors.length];
  const versionLabel =
    state.section === "retail" ? (state.version === "published" ? "发布时数据" : "修正后数据") : null;
  const metricMode = effectiveMode(seriesId);
  const modeLabel =
    isPropertySection()
      ? metricMode === "value"
        ? meta.valueLabel || "绝对值"
        : meta.yoyLabel || "同比"
      : state.mode === "value"
        ? "绝对值"
        : seriesId === "online_ex_auto_share"
          ? "同比变化"
          : "同比";
  const cadenceLabel =
    state.section === "retail"
      ? state.retailFrequency === "ttm"
        ? "TTM"
        : "当期"
      : state.section === "income"
        ? state.incomeFrequency === "quarter"
          ? "单季度"
          : state.incomeFrequency === "ttm"
            ? "TTM"
            : "年度"
        : null;
  const scaleLabel =
    state.section === "income" ? (state.incomeScale === "national" ? "全国" : "人均") : null;
  const city = cityFromSeriesId(seriesId);
  const citySeries = propertyCitySeries[baseSeriesId(seriesId)];
  const groupLabel = city && citySeries ? "城市明细" : meta.group;
  const metaLabels = [groupLabel, versionLabel, cadenceLabel, scaleLabel, modeLabel].filter(Boolean);
  const value = isCombinedHomePriceSeries(seriesId)
    ? combinedLines
        .map((line) => {
          const lineLatest = line.points[line.points.length - 1];
          return lineLatest ? `${line.label} ${formatMetric(line.id, lineLatest.value, effectiveMode(line.id))}` : `${line.label} --`;
        })
        .join(" / ")
    : latest
      ? formatMetric(seriesId, latest.value, metricMode)
      : "--";
  const latestCombinedLine = combinedLines.find((line) => line.points.length);
  const latestDisplay = isCombinedHomePriceSeries(seriesId)
    ? displayPeriod(latestCombinedLine?.points[latestCombinedLine.points.length - 1])
    : displayPeriod(latest);
  const tone = latest?.value >= 0 ? "up" : "down";
  const metricClass = isCombinedHomePriceSeries(seriesId) ? "" : state.mode === "yoy" ? tone : "";
  const source = sourceMeta(seriesId);

  card.innerHTML = `
    <div class="card-head">
      <div>
        <h2>${displaySeriesName(seriesId, meta)}</h2>
        <div class="meta">${metaLabels.join(" · ")}</div>
      </div>
      <div class="metric ${isCombinedHomePriceSeries(seriesId) ? "combined" : ""}">
        <strong class="${metricClass}">${value}</strong>
        <span>${latestDisplay}</span>
      </div>
    </div>
    ${
      isCombinedHomePriceSeries(seriesId)
        ? combinedUsable.length
          ? `<div class="chart"></div>`
          : `<div class="empty">该口径暂无可用${modeLabel}序列</div>`
        : points.length > 1
          ? `<div class="chart ${isPrimary ? "primary-chart" : ""}"></div>`
          : `<div class="empty">该口径暂无可用${modeLabel}序列</div>`
    }
    <div class="card-source">
      ${source.name}（<a href="${source.url}" target="_blank" rel="noreferrer">${source.label}</a>）
    </div>
  `;

  if (isCombinedHomePriceSeries(seriesId) && combinedUsable.length) {
    drawCombinedChart(card.querySelector(".chart"), combinedLines, seriesId);
  } else if (points.length > 1) {
    drawChart(card.querySelector(".chart"), points, color, seriesId);
  }
  return card;
}

function attachTooltip(container) {
  const svg = container.querySelector("svg");
  const focus = container.querySelector("[data-focus]");
  const tooltip = document.createElement("div");
  tooltip.className = "tooltip";
  container.appendChild(tooltip);

  const show = (target) => {
    const x = Number(target.dataset.x);
    const y = Number(target.dataset.y);
    const point = svg.createSVGPoint();
    point.x = x;
    point.y = y;
    const screenPoint = point.matrixTransform(svg.getScreenCTM());
    const box = container.getBoundingClientRect();
    const left = Math.max(76, Math.min(box.width - 76, screenPoint.x - box.left));
    const top = Math.max(44, screenPoint.y - box.top);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
    const note = target.dataset.supplement ? "<em>非完整发布稿口径</em>" : "";
    tooltip.innerHTML = `<span>${formatMixedSpacing(target.dataset.label || formatPeriod(target.dataset.period))}</span><strong>${target.dataset.value}</strong>${note}`;
    tooltip.classList.add("visible");
    focus.setAttribute("cx", target.dataset.x);
    focus.setAttribute("cy", target.dataset.y);
    if (target.dataset.color) focus.setAttribute("fill", target.dataset.color);
    focus.style.opacity = "1";
  };

  container.querySelectorAll(".hit-point").forEach((point) => {
    point.addEventListener("mouseenter", () => show(point));
    point.addEventListener("mousemove", () => show(point));
    point.addEventListener("mouseleave", () => {
      tooltip.classList.remove("visible");
      focus.style.opacity = "0";
    });
  });
}

function render() {
  updateHeadline();
  const grid = byId("chartGrid");
  grid.innerHTML = "";
  allSeriesIds().forEach((seriesId, index) => {
    grid.appendChild(makeCard(seriesId, index));
  });
  applyTypography(document.querySelector("main"));
}

function renderError(error) {
  byId("chartGrid").innerHTML = `<article class="card"><h2>数据加载失败</h2><p class="meta">${error.message}</p></article>`;
  applyTypography(document.querySelector("main"));
}

async function loadDataset(sectionId) {
  if (state.datasets[sectionId]) return;
  if (window.__DASHBOARD_DATA__?.[sectionId]) {
    state.datasets[sectionId] = window.__DASHBOARD_DATA__[sectionId];
    return;
  }
  if ((sectionId === "propertyPrice" || sectionId === "propertyCredit") && window.__DASHBOARD_DATA__?.property) {
    state.datasets[sectionId] = window.__DASHBOARD_DATA__.property;
    return;
  }
  const section = sections[sectionId];
  const response = await fetch(section.dataUrl);
  if (!response.ok) {
    throw new Error(`${section.label} 数据请求失败：${response.status}`);
  }
  state.datasets[sectionId] = await response.json();
}

function activateButtons(selector, activeButton) {
  document.querySelectorAll(selector).forEach((item) => item.classList.toggle("active", item === activeButton));
}

function bindControls() {
  document.querySelectorAll("[data-section]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.section = button.dataset.section;
      activateButtons("[data-section]", button);
      try {
        await loadDataset(state.section);
        render();
      } catch (error) {
        renderError(error);
      }
    });
  });
  document.querySelectorAll("[data-version]").forEach((button) => {
    button.addEventListener("click", () => {
      state.version = button.dataset.version;
      activateButtons("[data-version]", button);
      render();
    });
  });
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.mode = button.dataset.mode;
      activateButtons("[data-mode]", button);
      render();
    });
  });
  document.querySelectorAll("[data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      state.range = button.dataset.range;
      activateButtons("[data-range]", button);
      render();
    });
  });
  document.querySelectorAll("[data-retail-frequency]").forEach((button) => {
    button.addEventListener("click", () => {
      state.retailFrequency = button.dataset.retailFrequency;
      activateButtons("[data-retail-frequency]", button);
      render();
    });
  });
  document.querySelectorAll("[data-income-frequency]").forEach((button) => {
    button.addEventListener("click", () => {
      state.incomeFrequency = button.dataset.incomeFrequency;
      activateButtons("[data-income-frequency]", button);
      render();
    });
  });
  document.querySelectorAll("[data-income-scale]").forEach((button) => {
    button.addEventListener("click", () => {
      state.incomeScale = button.dataset.incomeScale;
      activateButtons("[data-income-scale]", button);
      render();
    });
  });
  byId("propertyCityButtons")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-city]");
    if (!button) return;
    state.propertyCity = button.dataset.city;
    render();
  });
}

async function boot() {
  await loadDataset(state.section);
  bindControls();
  render();
}

boot().catch((error) => {
  renderError(error);
});
