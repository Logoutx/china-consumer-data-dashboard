# Gate A report

- generated_at: 2026-07-10T17:06:16.692179+00:00
- release_id: 20260610_test-release
- touched series: 1
- verdict: **PASS** (exit 0)

| check | status | findings |
|---|---|---|
| gate_a.schema_series | pass | 0 |
| gate_a.caliber_declared | pass | 0 |
| gate_a.value_type_bounds | pass | 0 |
| gate_a.period_monotonic | pass | 0 |
| gate_a.unit_magnitude | skip | 0 |
| gate_a.seasonal_z | skip | 0 |
| gate_a.triangulate_dg_press | skip | 0 |
| gate_a.triangulate_pbc_nbs | skip | 0 |
| gate_a.ytd_arithmetic | skip | 0 |
| gate_a.yoy_base_tolerance | skip | 0 |
| gate_a.sum_of_parts | skip | 0 |
| gate_a.cpi_envelope | skip | 0 |
| gate_a.online_share_bounds | skip | 0 |
| gate_a.calendar_expected | pass | 0 |
| gate_a.calendar_window | pass | 0 |
| gate_a.partial_parse_completeness | pass | 0 |
| gate_a.archive_release_identity | pass | 0 |
| gate_a.break_no_yoy | skip | 0 |
| gate_a.break_link | skip | 0 |
| gate_a.revision_flood | skip | 0 |
| gate_a.revision_integrity | skip | 0 |
| gate_a.catalog_consistency | warn | 15 |

## Findings

- **WARN** `gate_a.catalog_consistency` : source_field '不包括食品和能源' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '交通通信' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '其他用品及服务' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '农村' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '医疗保健' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '城市' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '居住' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '教育文化娱乐' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '服务' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '消费品' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '生活用品及服务' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '衣着' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '非食品' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '食品' did not map to any series id
- **WARN** `gate_a.catalog_consistency` : source_field '食品烟酒及在外餐饮' did not map to any series id
