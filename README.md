# 中国消费数据看板

一个纯静态网页看板，用于长期追踪中国消费、居民收入支出、房价和房贷相关官方数据。

## 看板内容

- 社会消费品零售总额：社零总额、除汽车以外、汽车、城乡、线上、餐饮和商品零售。
- 居民收入和支出：居民人均可支配收入、收入结构、中位数、人均消费支出和八大消费支出分项。
- 70 城房价：70 城整体新房/二手房价格、上涨城市数，以及北上广深、省会和新一线、其他城市的城市明细。
- 房贷和土地出让：房贷余额、地产相关税收和土地出让收入。

## 打开方式

在本目录运行：

```bash
python3 -m http.server 8017
```

然后打开：

```text
http://127.0.0.1:8017/
```

## GitHub Pages

这是静态站点，推到 GitHub 后可以直接用 GitHub Pages 发布。项目站点通常是：

```text
https://<owner>.github.io/<repo>/
```

如果仓库是 private，GitHub Pages 可用性取决于账号/组织套餐。最稳妥的结构是：代码仓库保持 private，发布出的 Pages 站点作为公开链接分享。

## 数据来源

- 国家统计局“数据发布”归档：https://www.stats.gov.cn/sj/zxfb/
- 国家数据：https://data.stats.gov.cn/
- 中国人民银行《金融机构贷款投向统计报告》
- 财政部《财政收支情况》

所有数据均保留在本目录的 JSON/JS 静态文件中，页面打开时不依赖后端服务。

## 刷新脚本

```bash
python3 tools/fetch_retail_archive.py
python3 tools/fetch_income_archive.py
python3 tools/fetch_property_archive.py
python3 tools/merge_property_city_history.py
```

刷新 JSON 后，需要重新生成 `data.js`，让页面直接加载嵌入数据。

## 官方数据抽查 agent

运行随机审计：

```bash
python3 tools/audit_official_data.py --samples-per-pool 35 --seed 2026-06-22
```

它会生成 `audit_reports/official_data_audit_*.json` 和 `.md`，默认使用 `_cache/` 内已保存的官方页面和国家数据原始表。需要重新请求官方页面时：

```bash
python3 tools/audit_official_data.py --refresh-official --samples-per-pool 50
```

当前检查包括：

- 发布稿页面数值抽查：随机抽取发布时字段，确认数字能在官方页面文本中找到。
- 社零累计差分抽查：用相邻累计值复核当月值；该项只作为 warning，因为官方发布稿可能四舍五入或修订前期累计值。
- 70 城整体复算：用逐城新房/二手房环比数据重算 70 城平均和上涨城市数。
- 70 城逐城原始表复核：把看板逐城数据回查到国家数据原始表中的城市、月份、指标单元格。

## 修订处理

社零数据保留“修正后数据”和“发布时数据”两套口径。居民收入和支出当前没有发现发布时字段与最新字段的差异，因此页面不展示版本切换。

长期维护时建议保留：

- `observations_latest`：每次刷新后的最新有效序列，供看板默认展示。
- `observation_versions`：每次抓取的原始版本快照，记录旧值、新值、来源 URL 和抓取时间。
