from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import wechat_album_to_links
import wechat_article_to_markdown


ALBUM_URLS = [
    "https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz=MzUxNzQ5MTExNw==&scene=1&album_id=4410411814459375618&count=3&from=singlemessage#wechat_redirect"
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


def _fallback_album_title(url: str) -> str:
    album_id = _query_param(url, "album_id")
    biz = _query_param(url, "__biz")
    parts = []
    if biz:
        parts.append(biz)
    if album_id:
        parts.append(f"album_{album_id}")
    return "_".join(parts) or "wechat_album"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _download_album(
    album_url: str,
    *,
    timeout_ms: int,
    max_items: int,
    per_article_sleep: float,
    retries: int,
    retry_sleep: float,
) -> None:
    album_url = wechat_album_to_links.normalize_wechat_url(album_url)
    print("\n" + "=" * 80)
    print(f"合集: {album_url}")

    title, article_links = await wechat_album_to_links._extract_album(
        album_url,
        headless=True,
        timeout_ms=timeout_ms,
        max_items=max_items,
        debug=False,
    )

    safe_album_title = (
        wechat_article_to_markdown.sanitize_path_segment(title, max_len=80)
        or wechat_article_to_markdown.sanitize_path_segment(
            _fallback_album_title(album_url), max_len=80
        )
    )

    album_dir = OUTPUT_ROOT / safe_album_title
    album_dir.mkdir(parents=True, exist_ok=True)
    print(f"合集标题: {title}")
    print(f"输出目录: {album_dir}")
    print(f"文章链接数: {len(article_links)}")

    _save_json(
        album_dir / LINKS_FILENAME,
        {"title": title, "album_url": album_url, "article_links": article_links},
    )

    state_path = album_dir / STATE_FILENAME
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
            existing = album_dir / mapped_dir
            if existing.exists():
                print(f"\n[{idx}/{len(article_links)}] 已存在，跳过: {existing}")
                skipped += 1
                continue

        print(f"\n[{idx}/{len(article_links)}] 文章: {article_url}")
        max_attempts = max(1, retries + 1)
        for attempt in range(1, max_attempts + 1):
            try:
                article_dir = await wechat_article_to_markdown.fetch_article(
                    article_url,
                    output_dir=album_dir,
                    skip_if_exists=True,
                )
                state[article_url] = article_dir.name
                if (album_dir / article_dir.name).exists():
                    downloaded += 1
                break
            except Exception as e:
                is_last = attempt >= max_attempts
                if is_last:
                    print(f"下载失败: {e}")
                    failed += 1
                    failed_urls.append({"url": article_url, "error": str(e)})
                    break

                sleep_s = max(0.0, retry_sleep) * attempt + random.uniform(0.0, 1.0)
                print(f"失败将重试 ({attempt}/{max_attempts}): {e}")
                print(f"等待 {sleep_s:.1f}s 后重试...")
                await asyncio.sleep(sleep_s)

        # 定期落盘
        if idx % 5 == 0:
            _save_json(state_path, state)
            if failed_urls:
                _save_json(album_dir / FAILED_FILENAME, failed_urls)

        if per_article_sleep > 0:
            await asyncio.sleep(per_article_sleep)

    _save_json(state_path, state)
    if failed_urls:
        _save_json(album_dir / FAILED_FILENAME, failed_urls)
    print("\n" + "-" * 80)
    print(f"完成合集: {safe_album_title}")
    print(f"下载: {downloaded} | 跳过: {skipped} | 失败: {failed}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wechat-album-batch-crawl",
        description="批量提取公众号合集文章链接并下载为 Markdown（按合集标题分目录）",
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="合集页 URL（可传多个；若不传则使用脚本内 ALBUM_URLS 列表）",
    )
    parser.add_argument("--timeout-ms", type=int, default=60_000, help="合集页打开超时（毫秒）")
    parser.add_argument("--max-items", type=int, default=500, help="每个合集最多拉取文章条数")
    parser.add_argument(
        "--per-article-sleep",
        type=float,
        default=1.0,
        help="每篇文章下载后的等待秒数（降低风控概率）",
    )
    parser.add_argument("--retries", type=int, default=2, help="单篇文章失败重试次数")
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=8.0,
        help="失败重试等待基数（秒，实际会乘以 attempt 并加少量随机抖动）",
    )
    args = parser.parse_args()

    urls = args.urls if args.urls else ALBUM_URLS
    if not urls:
        print("请提供至少一个合集页 URL（通过命令行参数或编辑脚本内 ALBUM_URLS 列表）", file=sys.stderr)
        sys.exit(1)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    for album_url in urls:
        try:
            await _download_album(
                album_url,
                timeout_ms=args.timeout_ms,
                max_items=max(1, args.max_items),
                per_article_sleep=max(0.0, args.per_article_sleep),
                retries=max(0, args.retries),
                retry_sleep=max(0.0, args.retry_sleep),
            )
        except Exception as e:
            print(f"合集处理失败: {album_url}\n原因: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
