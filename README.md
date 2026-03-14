# wechat-article-to-markdown

Fetch WeChat Official Account articles and convert them to clean Markdown.

[English](#features) | [中文](#功能特性)

## Features

- Anti-detection fetching with Camoufox
- Extract article metadata (title, account name, publish time, source URL)
- Convert WeChat article HTML to Markdown
- Download article images to local `images/` and rewrite links
- Handle WeChat `code-snippet` blocks with language fences

## Installation

```bash
# Recommended: uv tool (fast, isolated)
uv tool install wechat-article-to-markdown

# Or: pipx
pipx install wechat-article-to-markdown
```

Or from source:

```bash
git clone git@github.com:jackwener/wechat-article-to-markdown.git
cd wechat-article-to-markdown
uv sync
```

## Usage

```bash
# Installed CLI
wechat-article-to-markdown "https://mp.weixin.qq.com/s/xxxxxxxx"

# Run in repo with uv
uv run wechat-article-to-markdown "https://mp.weixin.qq.com/s/xxxxxxxx"

# Backward-compatible local entry
uv run main.py "https://mp.weixin.qq.com/s/xxxxxxxx"
```

Extract links from an Official Account homepage (JSON includes `article_links` and `all_links`):

```bash
wechat-homepage-to-links "https://mp.weixin.qq.com/mp/homepage?__biz=xxxx&hid=xx&sn=xx" --json

# Or run in repo without installing the CLI
python wechat_homepage_to_links.py "https://mp.weixin.qq.com/mp/homepage?__biz=xxxx&hid=xx&sn=xx" --json
```

Batch crawl multiple homepages into `output/<homepage-title>/<article-title>/`:

1) Edit `HOMEPAGE_URLS` in `wechat_batch_crawl.py`
2) Run:

```bash
python -u wechat_batch_crawl.py
```

Optional flags (recommended for stability):

```bash
python -u wechat_batch_crawl.py --max-items 500 --per-article-sleep 1.0 --retries 2 --retry-sleep 8
```

The batch script will skip downloading when the target directory already exists, so it is safe to rerun.

Output structure (single article):

```text
output/
└── <article-title>/
    ├── <article-title>.md
    └── images/
        ├── img_001.png
        ├── img_002.png
        └── ...
```

Output structure (batch crawl):

```text
output/
└── <homepage-title>/
    ├── article_links.json
    ├── download_state.json
    ├── failed_urls.json
    ├── debug.html
    └── <article-title>/
        ├── <article-title>.md
        └── images/
            ├── img_001.png
            ├── img_002.png
            └── ...
```


## Testing

```bash
# Unit tests (default CI path)
uv run --with pytest pytest -q -m "not e2e"

# Live E2E against real WeChat articles
WECHAT_E2E_URLS="https://mp.weixin.qq.com/s/Y7dyRC7CJ09miHWU6LBzBA,https://mp.weixin.qq.com/s/xxxxxxxx" \
  uv run --with pytest pytest -q -m e2e -s
```

`e2e` tests require network and browser runtime, so they run via manual GitHub Actions workflow `.github/workflows/e2e.yml`.

## Use as AI Agent Skill

This project ships with [`SKILL.md`](./SKILL.md), so AI agents can discover and use this tool workflow.

### Claude Code / Antigravity

```bash
# Project-local skills directory
mkdir -p .agents/skills
git clone git@github.com:jackwener/wechat-article-to-markdown.git \
  .agents/skills/wechat-article-to-markdown

# Or copy SKILL.md only
curl -o .agents/skills/wechat-article-to-markdown/SKILL.md \
  https://raw.githubusercontent.com/jackwener/wechat-article-to-markdown/main/SKILL.md
```

```bash
# Claude Code user-level skills directory (global)
mkdir -p ~/.claude/skills/wechat-article-to-markdown
curl -o ~/.claude/skills/wechat-article-to-markdown/SKILL.md \
  https://raw.githubusercontent.com/jackwener/wechat-article-to-markdown/main/SKILL.md
```

After adding the file, restart Claude Code to reload skills.

### OpenClaw / ClawHub

Officially supports [OpenClaw](https://openclaw.ai) and [ClawHub](https://docs.openclaw.ai/tools/clawhub):

```bash
clawhub install wechat-article-to-markdown
```

## PyPI Publishing (GitHub Actions)

Repository: `jackwener/wechat-article-to-markdown`
Workflow: `.github/workflows/release.yml`
Environment: `pypi`

`release.yml` triggers on `v*` tags, runs unit tests + live e2e tests, then publishes to PyPI with trusted publishing (`id-token: write`).

For release e2e targets, set repository variable `RELEASE_E2E_URLS` (comma-separated article URLs).  
If not set, workflow falls back to `https://mp.weixin.qq.com/s/Y7dyRC7CJ09miHWU6LBzBA`.

---

## 功能特性

- 使用 Camoufox 进行反检测抓取
- 提取标题、公众号名称、发布时间、原文链接
- 将微信公众号文章 HTML 转换为 Markdown
- 下载图片到本地 `images/` 并自动替换链接
- 处理微信 `code-snippet` 代码块并保留语言标识

## 安装

```bash
# 推荐：uv tool
uv tool install wechat-article-to-markdown

# 或者：pipx
pipx install wechat-article-to-markdown
```

## 使用示例

```bash
wechat-article-to-markdown "https://mp.weixin.qq.com/s/xxxxxxxx"
```

提取公众号合集的推文链接（JSON 输出包含 `article_links` 和 `all_links`）：

```bash
wechat-homepage-to-links "https://mp.weixin.qq.com/mp/homepage?__biz=xxxx&hid=xx&sn=xx" --json

# 或者在仓库内直接运行脚本（无需安装 CLI）
python wechat_homepage_to_links.py "https://mp.weixin.qq.com/mp/homepage?__biz=xxxx&hid=xx&sn=xx" --json
```

批量下载多个合集下的全部推文（输出到 `output/<合集标题>/<推文标题>/`）：

1) 编辑 `wechat_batch_crawl.py` 里的 `HOMEPAGE_URLS`（填入要抓取的合集列表）
2) 运行：

```bash
python -u wechat_batch_crawl.py
```

可选参数（建议保守一些，降低风控/超时概率）：

```bash
python -u wechat_batch_crawl.py --max-items 500 --per-article-sleep 1.0 --retries 2 --retry-sleep 8
```

脚本会在每篇推文下载前检查目标目录是否已存在，存在则跳过，因此可重复运行做增量更新。

## 作为 AI Agent Skill 使用

项目自带 [`SKILL.md`](./SKILL.md)，可供支持 `.agents/skills/` 约定的 Agent 自动发现。

### Claude Code 用户目录示例

```bash
mkdir -p ~/.claude/skills/wechat-article-to-markdown
curl -o ~/.claude/skills/wechat-article-to-markdown/SKILL.md \
  https://raw.githubusercontent.com/jackwener/wechat-article-to-markdown/main/SKILL.md
```

添加后重启 Claude Code 以重新加载 skills。

### OpenClaw / ClawHub

```bash
clawhub install wechat-article-to-markdown
```

## License

MIT
