# HANDOFF — 在 Mac mini 上接手本项目

写于 2026-07-14。交接原因：开发机从 MacBook 换到 Mac mini。数据更新完全跑在 GitHub Actions 上，跟哪台电脑无关；需要在新机器上恢复的只有本地开发环境。

## 项目现状

自动更新、双重校验、NYT 风格呈现的中国官方消费/经济数据看板。89 个数据序列 + 70 城房价面板，8 个板块；新版网页在 `site/`（每个板块一个页面），旧版仍在仓库根目录，等正式发布后删除。数据最早到 1985 年。

已实弹验证的自动更新：6 月 CPI（提交 022a4ff）、社零限额以上口径补齐（10ea703）、DG 接口 6 月批次（676ff87 及之后的定时提交）都由定时任务自动抓取、通过质量检查后入库，无人工干预。

## 自动化在跑什么（与本机无关）

- 每天北京时间 8-18 点，每 2 小时一次定时任务：查发布窗口 → 抓取官方数据 → 暂存校验（入库前 22 项检查）→ 提交。
- 发布前另有 10 项独立复核（含与原始存档逐值比对、衍生数值重算、新旧数据一致性）。
- 任何拦截都会：跑红 + 发邮件 + 在仓库开 “数据门禁拦截” issue，并且不写入任何数据。之前两次事故（推送失败、DG 存档清单缺失）均已修复并回归测试。
- 测试基线：587 个通过。

## 在 Mac mini 上恢复开发环境

Dropbox 会把工作文件同步过去，但按规矩 `.git` 不走 Dropbox。恢复 git 的推荐做法（避免动 Dropbox 里的文件夹名）：

```sh
cd ~/Library/CloudStorage/Dropbox/Projects/china-consumer-data-dashboard
git clone --no-checkout https://github.com/Logoutx/china-consumer-data-dashboard.git /tmp/ccd-tmp
mv /tmp/ccd-tmp/.git .git
rm -rf /tmp/ccd-tmp
git checkout main -- . 2>/dev/null || true   # 索引对齐；工作文件本来就与 main 一致
git status                                    # 应当干净（或仅有本机未跟踪文件）
xattr -w com.dropbox.ignored 1 .git           # 关键：让 Dropbox 忽略 .git
xattr -p com.dropbox.ignored .git             # 应输出 1
xattr -w com.dropbox.ignored 1 _cache 2>/dev/null || true
xattr -w com.dropbox.ignored 1 audit_reports 2>/dev/null || true
```

然后：

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 本地跑构建和测试用
gh auth status                                                        # 确认 GitHub CLI 已登录
.venv/bin/python -m pipeline.build --out site-data/                   # 生成网页数据
python3 -m http.server 8123                                           # 预览 http://localhost:8123/site/
```

如果要用 Claude 的预览面板，在 Mac mini 的 `~/.claude/launch.json` 里加一条（此文件不跨机同步）：

```json
{
  "name": "china-dashboard",
  "runtimeExecutable": "python3",
  "runtimeArgs": ["-m", "http.server", "8123", "--directory", "/Users/<用户名>/Library/CloudStorage/Dropbox/Projects/china-consumer-data-dashboard"],
  "port": 8123
}
```

## 观察与待办

- **本周实弹**：6 月经济活动数据 + 二季度 GDP 在 7 月 15-18 日窗口发布，是新闻稿解析路线（nbs_retail）第一次大规模实战。收到红色运行邮件就看 issue 和运行日志——之前每次红都定位到了真问题。
- **正式发布（唯一需要人做的决定）**：仓库 Settings → Pages → Source 选 GitHub Actions，然后 `gh variable set PAGES_LIVE -b 1`。仓库目前是私有的，私有仓库开 Pages 需要付费套餐，或者把仓库转公开。步骤在 docs/OPERATIONS.md。
- 高频脉搏板块还没有数据来源（快递、票房、假期出行等，暂无可靠官方抓取路径）。
- 消费者信心指数没有可用来源（DG 接口树里不存在，也没有新闻稿），目前不在目录里。
- 当月/累计切换目前只换读数文本，不换曲线（数据包每序列只带一种口径的数组）。
- 定时提交的标题里 dg_refresh 批次显示 “@unknown”，纯外观问题。
- `spike/ci-reachability` 分支是早期探测用的，可以删。

## 文档地图

- README.md — 项目总览（中文，先读这个）
- docs/DATA-CONTRACT.md — 数据模型：口径、断点、修订、每序列文件格式
- docs/ACQUISITION.md — 每个数据源怎么抓、历史怎么回填
- docs/OPERATIONS.md — 运维手册：定时任务、质量检查语义、发布切换、故障处理
- docs/VIZ-GUIDE.md — 设计规范（15 条 NYT 规则 + 颜色字体标准）
- site/DEV-NOTES.md — 前端自查清单 + 89 序列可见性核对
- pipeline/migrate/REPORT.md、pipeline/backfill/REPORT.md — 数据迁移与回填的完整记录，含已知数据异常

## MacBook 侧的遗留说明

2026-07-14 交接时，MacBook 上的 Dropbox 把仓库里大量归档文件转成了在线占位文件且恢复缓慢，导致本机 git 操作卡住。因此本文件是直接通过 GitHub 接口提交的，MacBook 的本地副本可能落后若干提交——Dropbox 恢复后 `git pull` 即可，不需要其他处理。Mac mini 全新克隆不受影响。
