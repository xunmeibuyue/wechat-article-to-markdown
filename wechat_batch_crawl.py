from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import wechat_article_to_markdown
import wechat_homepage_to_links


HOMEPAGE_URLS = [
]

OUTPUT_ROOT = Path.cwd() / "output"
STATE_FILENAME = "download_state.json"
LINKS_FILENAME = "article_links.json"
FAILED_FILENAME = "failed_urls.json"


def _query_param(url: str, key: str) -> str:
    try:
        parsed = urlparse(url)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            if k == key:
                return v
    except Exception:
        return ""
    return ""


def _fallback_homepage_title(url: str) -> str:
    hid = _query_param(url, "hid")
    biz = _query_param(url, "__biz")
    parts = []
    if biz:
        parts.append(biz)
    if hid:
        parts.append(f"hid_{hid}")
    return "_".join(parts) or "wechat_homepage"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _extract_homepage_title_and_links(
    homepage_url: str,
    *,
    timeout_ms: int,
    max_items: int,
) -> tuple[str, list[str]]:
    title, article_links, _page_links, _all_links = await wechat_homepage_to_links._extract_homepage(
        homepage_url,
        headless=True,
        timeout_ms=timeout_ms,
        max_items=max_items,
        debug=False,
    )
    return title, article_links


async def _download_homepage(
    homepage_url: str,
    *,
    timeout_ms: int,
    max_items: int,
    per_article_sleep: float,
    retries: int,
    retry_sleep: float,
) -> None:
    homepage_url = wechat_homepage_to_links.normalize_wechat_url(homepage_url)
    print("\n" + "=" * 80)
    print(f"🏠 主页: {homepage_url}")

    title, article_links = await _extract_homepage_title_and_links(
        homepage_url, timeout_ms=timeout_ms, max_items=max_items
    )
    safe_homepage_title = (
        wechat_article_to_markdown.sanitize_path_segment(title, max_len=80)
        or wechat_article_to_markdown.sanitize_path_segment(
            _fallback_homepage_title(homepage_url), max_len=80
        )
    )

    homepage_dir = OUTPUT_ROOT / safe_homepage_title
    homepage_dir.mkdir(parents=True, exist_ok=True)
    print(f"📄 主页标题: {title}")
    print(f"📁 输出目录: {homepage_dir}")
    print(f"🔗 推文链接数: {len(article_links)}")

    _save_json(homepage_dir / LINKS_FILENAME, {"title": title, "homepage_url": homepage_url, "article_links": article_links})

    state_path = homepage_dir / STATE_FILENAME
    state = _load_json(state_path)
    if not isinstance(state, dict):
        state = {}

    failed_urls: list[dict[str, str]] = []

    skipped = 0
    downloaded = 0
    failed = 0

    for idx, article_url in enumerate(article_links, start=1):
        article_url = wechat_article_to_markdown.normalize_wechat_url(article_url)
        mapped_dir = state.get(article_url)
        if isinstance(mapped_dir, str) and mapped_dir.strip():
            existing = homepage_dir / mapped_dir
            if existing.exists():
                print(f"\n[{idx}/{len(article_links)}] ⏭️ 已存在，跳过: {existing}")
                skipped += 1
                continue

        print(f"\n[{idx}/{len(article_links)}] 📰 推文: {article_url}")
        max_attempts = max(1, retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                article_dir = await wechat_article_to_markdown.fetch_article(
                    article_url,
                    output_dir=homepage_dir,
                    skip_if_exists=True,
                )
                state[article_url] = article_dir.name
                if (homepage_dir / article_dir.name).exists():
                    downloaded += 1
                break
            except Exception as e:
                is_last = attempt >= max_attempts
                if is_last:
                    print(f"❌ 下载失败: {e}")
                    failed += 1
                    failed_urls.append({"url": article_url, "error": str(e)})
                    break

                sleep_s = max(0.0, retry_sleep) * attempt + random.uniform(0.0, 1.0)
                print(f"⚠️ 失败将重试 ({attempt}/{max_attempts}): {e}")
                print(f"⏳ 等待 {sleep_s:.1f}s 后重试...")
                await asyncio.sleep(sleep_s)

        # 定期落盘，避免中途被验证码/崩溃导致状态丢失
        if idx % 5 == 0:
            _save_json(state_path, state)
            if failed_urls:
                _save_json(homepage_dir / FAILED_FILENAME, failed_urls)

        if per_article_sleep > 0:
            await asyncio.sleep(per_article_sleep)

    _save_json(state_path, state)
    if failed_urls:
        _save_json(homepage_dir / FAILED_FILENAME, failed_urls)
    print("\n" + "-" * 80)
    print(f"✅ 完成主页: {safe_homepage_title}")
    print(f"📊 下载: {downloaded} | 跳过: {skipped} | 失败: {failed}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog="task-260314-task1",
        description="批量提取公众号主页推文链接并下载为 Markdown（按主页标题分目录）",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="主页打开超时（毫秒）")
    parser.add_argument("--max-items", type=int, default=500, help="每个主页最多拉取推文条数")
    parser.add_argument(
        "--per-article-sleep",
        type=float,
        default=1.0,
        help="每篇推文下载后的等待秒数（降低风控概率）",
    )
    parser.add_argument("--retries", type=int, default=2, help="单篇推文失败重试次数")
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=8.0,
        help="失败重试等待基数（秒，实际会乘以 attempt 并加少量随机抖动）",
    )
    args = parser.parse_args()

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    for homepage_url in HOMEPAGE_URLS:
        try:
            await _download_homepage(
                homepage_url,
                timeout_ms=args.timeout_ms,
                max_items=max(1, args.max_items),
                per_article_sleep=max(0.0, args.per_article_sleep),
                retries=max(0, args.retries),
                retry_sleep=max(0.0, args.retry_sleep),
            )
        except Exception as e:
            print(f"❌ 主页处理失败: {homepage_url}\n原因: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
