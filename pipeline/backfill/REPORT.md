# Backfill report -- NBS DG API

Generated 2026-07-07T19:23:24Z. 26 series built, 4820 total observations, 4 targets skipped.

## Series produced

| id | cid(s) | indicator id(s) | range | obs | notes |
|---|---|---|---|---|---|
| `nbs-cpi-yoy` | `42132fae9f2244818f0480b4c422615c, 5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083, 954cfd7597e34b919ec71caf6aeead51, 9d4eec43537742a7ab5d63db97fa2f51, b4fad2cf9e0e4af7815b7e9e2e95c5c7, bc985d1741a94451880c606022a8fe00, e6664817f0cd427783cd397770695634` | `0dc091b5194c46afaf10369d5c55676a, 384ddbda2edc47969caa98263f16231b, 4ae9047687934a6390984c21d6ddab96, 4c1065dd4e984b25a21190c843551697, 53180dfb9c14411ba4b762307c85920c, e437d965279d41ceb9ace591b62f6ffc, e5c318ffdbbc4d38898e52b52267eb25, f3904a1f5a384d54a3944ec6e2df3d1c` | 1990-01..2026-05 | 437 | m=index(100 basis); m_yoy derived |
| `nbs-cpi-food-yoy` | `5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083, 9d4eec43537742a7ab5d63db97fa2f51` | `00f5d26484104d8b8cced5f1658890aa, 42c2d9b5d1b749c4b68c2cbd2e3d4a42, fce9ac527a74442ea0031eb6b37f52ad` | 2016-01..2026-05 | 125 | m=index(100 basis); m_yoy derived |
| `nbs-cpi-nonfood-yoy` | `5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083` | `728da4f1859140139194110824b700e1, f91a869a255949ccba0cd73cfa871340` | 2021-01..2026-05 | 65 | m=index(100 basis); m_yoy derived |
| `nbs-cpi-core-yoy` | `5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083` | `71be3d43d2fb44188199840272463ae0, c2050e97c49a4763a6d0f0f38bf0b4ed` | 2021-01..2026-05 | 65 | m=index(100 basis); m_yoy derived |
| `nbs-cpi-services-yoy` | `5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083` | `c87191b714554e9eba8c2c062abfabb4, e330ad10ab224cfda1f25db93bf04d01` | 2021-01..2026-05 | 65 | m=index(100 basis); m_yoy derived |
| `nbs-cpi-goods-yoy` | `5c7452825c7c4dcba391db5ca7f335c5, 809d2522b0fe4be89142650341b19083` | `24371d6f24ce4f11921cb6194bb1e96b, 61a170ad7fa44fa4b2ff60fd708e516e` | 2021-01..2026-05 | 65 | m=index(100 basis); m_yoy derived |
| `nbs-ppi-yoy` | `60e8b361f11c4a878c652a6487a25561, 677cfbb4f06941af8c1761c4804e58cf` | `150633e52b9a470a9a9fd1b296dd6c5b, e64079bae9064aebad1c4c5fe0c8a6ef` | 1993-01..2026-05 | 401 | m=index(100 basis); m_yoy derived; no date windows needed (single cid to 1996) |
| `nbs-ppi-producer-yoy` | `60e8b361f11c4a878c652a6487a25561` | `47f8464961184392bc4f6a4b8e5b1cb5` | 1996-10..2026-05 | 356 | m=index(100 basis); m_yoy derived; no date windows needed (single cid to 1996) |
| `nbs-ppi-consumer-yoy` | `60e8b361f11c4a878c652a6487a25561` | `649b27dbffb14466a3f204e16f3c74ed` | 1996-10..2026-05 | 356 | m=index(100 basis); m_yoy derived; no date windows needed (single cid to 1996) |
| `cflp-pmi-mfg` | `93ffbb1aa85740d3aa2618371508b606` | `a09aa989bdcf4cffa2021795722eb916` | 2005-01..2026-06 | 258 | diffusion index, no YoY published |
| `cflp-pmi-nonmfg` | `7a64a6e25aec4a8e9dde044ecd9e2cce` | `88a150208f6e4a1db8babe41ae700f66` | 2007-01..2026-06 | 234 | diffusion index, no YoY published |
| `nbs-urban-unemp` | `ee3b7046b390415b9b7745e3d16f6052` | `3888eac6062945a79c8a27e5f13d4953` | 2018-01..2026-05 | 101 |  |
| `nbs-urban-unemp-31city` | `ee3b7046b390415b9b7745e3d16f6052` | `1d550f3ec77a463bb607d4a3427e1465` | 2018-01..2026-05 | 101 |  |
| `nbs-urban-unemp-youth-1624` | `ee3b7046b390415b9b7745e3d16f6052` | `bd6da1abb26046c2acb38aa701d90e86` | 2018-01..2023-06 | 66 | frozen old-basis series, superseded_by exstudent id |
| `nbs-urban-unemp-youth-1624-exstudent` | `ee3b7046b390415b9b7745e3d16f6052` | `bd6da1abb26046c2acb38aa701d90e86` | 2023-12..2026-05 | 30 | new-basis series, break_first flagged on 2023-12 |
| `nbs-industrial-va` | `3f2e14f0542348ed9fe02476eca3450b` | `ef1b1765960d45a29b4d7c4ca91be916, 21e7072e9f384209aedb56e69a18216e` | 2015-02..2026-05 | 125 | growth-rate-only series (no level published) |
| `nbs-fai` | `5129067b149d4ddfbec1ffc478d35bfb` | `7e570cf8071c4734a7d78d9f0a70fbe1` | 2015-02..2026-05 | 125 | YTD-YoY only, no level published anywhere in the FAI tree |
| `customs-exports-usd` | `7e11b47c828d4e4e925f1c5a98305558` | `9e38b39f55a7461ea195508c1bb7dbdc, 788f44b0f310403fbd308b77d6f83890, a349b30535404467b37e68871e99762c, d857d76d08af4f3b92b24fa29e0ca177` | 2000-01..2026-05 | 317 | USD only (千美元->亿美元); RMB variant not in this DG tree |
| `customs-imports-usd` | `7e11b47c828d4e4e925f1c5a98305558` | `86a340fee806409ebdf4d0069bd23f29, cc1ac699bc4f4cd7aa5c2b7a1e643259, 6545926705124075961bf28602fd50bb, 5273a0f169234ea5abfa6d966cb0e93a` | 2000-01..2026-05 | 317 | USD only (千美元->亿美元); RMB variant not in this DG tree |
| `pbc-m0` | `82130c6621a745cda3d64b090e733383` | `bd67997414b147a08d4aa03d146f4486, db7891fb8f3c4eb2a4d71a9955eba8c7` | 1999-12..2026-04 | 317 |  |
| `pbc-m1` | `82130c6621a745cda3d64b090e733383` | `add08d4a1ca049158166f126e169edde, 640401d3351b4b868dea28f89f410a54` | 1999-12..2026-04 | 317 |  |
| `pbc-m2` | `82130c6621a745cda3d64b090e733383` | `f3c0ae453a54424489af41de315ec592, e03f2232631f41cd9d754a7d7feb4a81` | 1999-12..2026-04 | 317 |  |
| `nbs-gdp` | `28d936104e304aa191e338eb82b6dc09, f9b694c9b79e4ce5958bc88c6410fa67` | `d22612f09aeb4241bc557ef0ac61b3ba, 8c5fab362d124fa7b91af833b3bd7397, 170e7f00f8c24ede863c0526b42ae81f` | 1992-Q1..2026-Q1 | 137 | real_yoy derived from constant-price index(100 basis) |
| `nbs-gdp-contribution-consumption` | `62cd73e3fcb4492fab8e998ca3a7dc5b` | `ce94d6319c5e4a55b271c6a0a0f671c2, 74879952748d45eeb193c165c00196a8` | 2016-Q1..2026-Q1 | 41 |  |
| `nbs-gdp-contribution-investment` | `62cd73e3fcb4492fab8e998ca3a7dc5b` | `cd5cb165190b4a9ab8f2f5674c58618b, a5eb634225de4c24a2827dc8ba979e19` | 2016-Q1..2026-Q1 | 41 |  |
| `nbs-gdp-contribution-netexports` | `62cd73e3fcb4492fab8e998ca3a7dc5b` | `8df845d93daa42359314690b9721de19, 35271309c7754640a997fdb316f43208` | 2016-Q1..2026-Q1 | 41 |  |

## Skipped / failed targets

- **Consumer confidence index (+ sub-indices)**: Searched all 14 monthly-tree domains (价格指数/工业/能源/固定资产投资/服务业生产指数/城镇调查失业率/房地产/国内贸易/对外经济/交通运输/邮电通信/采购经理指数/财政/金融) plus the quarterly tree's 国民经济核算/人民生活/文化/国内贸易 branches -- no 景气指数 or 消费者信心 node anywhere in this DG catalog. Likely not mirrored into data.stats.gov.cn's public DG tree at all (NBS may only publish it via a non-DG channel). Not brute-forced further per budget guidance.
- **社融 (aggregate financing to the real economy) stock + flow**: 金融 (Finance) domain under 月度数据 has exactly one leaf, 货币供应量 (M0/M1/M2) -- confirmed by re-querying its children directly (count=1). 社融 is a PBoC-specific release not mirrored into this NBS DG catalog; per docs/ACQUISITION.md Group 6 it needs the wzdig.pbc.gov.cn search+parse route instead, out of scope for a DG-only backfill.
- **Income / consumption expenditure (national/urban/rural, quarterly YTD)**: Per task instructions: docs/MIGRATION-MAP.md §6b already covers all income_disposable / consumption_expenditure series (and their urban/rural splits) from the existing archive, 2013-> quarterly. Confirmed present in the quarterly DG tree too (人民生活 domain) but intentionally not duplicated here -- would collide with the migration agent's ids.
- **Retail family (社会消费品零售总额 etc.)**: Owned by docs/MIGRATION-MAP.md §6a / the migration agent; not touched by this backfill agent (explicit instruction not to duplicate ids the migration already covers).

## Oddities flagged during the run

- pbc-m1 basis check: OLD-BASIS then re-definition: 2024-12=670959.4亿元 -> 2025-01=1124457.4亿元 (+67.6%，远超此前典型环比 ~1.81%）——历史看起来是旧口径，2025-01 是新口径下的第一个印数，DG 库未见对 2024-01~2024-12 的追溯改写。
- docs/MIGRATION-MAP.md §8 lists nbs-urban-unemp-2534/nbs-urban-unemp-3159 assuming a 25-34 age bracket; the DG tree's actual age brackets are 25—29岁, 30—59岁, and 25—59岁 (no 25-34, no 34 at all). Flagging the mismatch for whoever wires that part of the catalog -- not minted here since the task only asked for national/31-city/youth-exstudent unemployment.
