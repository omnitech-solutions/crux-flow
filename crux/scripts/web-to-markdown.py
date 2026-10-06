#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31",
#     "beautifulsoup4>=4.12",
#     "html2text>=2024.2",
# ]
# ///
"""Convert a web page to clean Markdown for the Idyll vault.

Usage:
    web-to-markdown.py <url> [--output-dir DIR] [--no-images] [--allow-private]

Behavior:
    - Validates the URL (only http/https; blocks private/loopback IPs by default).
    - Fetches the page with a 10 MB response cap and per-redirect-hop validation.
    - Extracts the main content using readability heuristics.
    - Preserves code-block language hints (`<pre><code class="language-X">` → ```X fences).
    - Downloads referenced images into --output-dir (unless --no-images).
    - Writes the markdown body to stdout; writes a JSON metadata block to stderr.

When --output-dir is provided, also saves:
    <output-dir>/source.html   raw fetched HTML
    <output-dir>/source.url    the canonical URL (plus final URL if redirected)
    <output-dir>/<image-name>  each downloaded image, content-hashed to avoid collisions

Exit codes:
    0  success
    1  URL validation, network, or extraction error
    2  thin-content warning (less than 200 words extracted); markdown still written
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit

import requests
from bs4 import BeautifulSoup, NavigableString
import html2text


MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = 30
THIN_CONTENT_WORDS = 200
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
    )


def _validate_url(url: str, allow_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme!r} (only http/https)")
    host = parts.hostname
    if not host:
        raise ValueError("URL missing host")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for {host}: {e}")
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(ip):
            raise ValueError(
                f"refusing to fetch {host}: resolves to private/loopback IP {ip}. "
                "Pass --allow-private to override."
            )


def _fetch(url: str, allow_private: bool) -> tuple[str, bytes, str, str]:
    """Fetch the URL. Returns (final_url, raw_bytes, detected_encoding, content_type)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _validate_url(current, allow_private)
        resp = requests.get(
            current,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=REQUEST_TIMEOUT,
            allow_redirects=False,
            stream=True,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                raise RuntimeError(f"redirect {resp.status_code} with no Location")
            current = urljoin(current, loc)
            continue
        resp.raise_for_status()
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=64 * 1024, decode_unicode=False):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"response exceeded {MAX_RESPONSE_BYTES} bytes; aborting"
                )
        resp._content = bytes(body)
        # requests defaults to ISO-8859-1 when no charset header is present, which
        # turns UTF-8 apostrophes/arrows into mojibake. Prefer charset header,
        # then apparent_encoding (chardet), then utf-8.
        encoding = resp.encoding
        if not encoding or encoding.upper() in ("ISO-8859-1", "LATIN-1"):
            encoding = resp.apparent_encoding or "utf-8"
        content_type = resp.headers.get("Content-Type", "")
        return current, bytes(body), encoding, content_type
    raise RuntimeError(f"too many redirects (>{MAX_REDIRECTS})")


def _extract_metadata(soup: BeautifulSoup, url: str) -> dict:
    md = {
        "url": url,
        "title": "",
        "author": None,
        "published_date": None,
        "description": None,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    title = soup.find("title")
    if title:
        md["title"] = title.get_text(strip=True)
    candidates = {
        "author": ["author", "article:author", "dc.creator", "twitter:creator"],
        "description": ["description", "og:description", "twitter:description"],
        "published_date": [
            "article:published_time",
            "datePublished",
            "pubdate",
            "date",
            "publish_date",
        ],
    }
    for key, names in candidates.items():
        for name in names:
            tag = soup.find("meta", attrs={"name": name}) or soup.find(
                "meta", attrs={"property": name}
            )
            if tag and tag.get("content"):
                md[key] = tag["content"].strip()
                break
    return md


def _extract_main(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "form",
            "nav",
            "footer",
            "header",
            "aside",
            "svg",
        ]
    ):
        tag.decompose()
    for selector in (
        "main",
        "article",
        '[role="main"]',
        ".main-content",
        ".post-content",
        ".entry-content",
        ".article-content",
        ".markdown-body",
        "#content",
    ):
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    return soup.find("body") or soup


def _absolutize(soup: BeautifulSoup, base_url: str) -> None:
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            img["src"] = urljoin(base_url, src)
    for a in soup.find_all("a"):
        href = a.get("href")
        if href:
            a["href"] = urljoin(base_url, href)


def _preserve_code_languages(soup: BeautifulSoup) -> None:
    for pre in soup.find_all("pre"):
        code = pre.find("code")
        if not code:
            continue
        classes = code.get("class", []) or []
        for cls in classes:
            if cls.startswith(("language-", "lang-", "highlight-source-")):
                lang = re.sub(r"^(language-|lang-|highlight-source-)", "", cls)
                pre.insert_before(NavigableString(f"\n<!--CODELANG:{lang}-->\n"))
                break


_CODELANG_RE = re.compile(
    r"<!--CODELANG:([a-zA-Z0-9_+-]+)-->\s*\n((?:(?:    |\t).*(?:\n|$))+)",
    re.MULTILINE,
)


def _apply_code_fences(md: str) -> str:
    def replace(match: re.Match) -> str:
        lang = match.group(1)
        body = match.group(2)
        lines = []
        for line in body.splitlines():
            if line.startswith("    "):
                lines.append(line[4:])
            elif line.startswith("\t"):
                lines.append(line[1:])
            else:
                lines.append(line)
        return f"```{lang}\n" + "\n".join(lines).rstrip() + "\n```\n"

    return _CODELANG_RE.sub(replace, md)


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _download_images(soup: BeautifulSoup, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    count = 0
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or not src.startswith(("http://", "https://")):
            continue
        if src in seen:
            img["src"] = seen[src]
            continue
        try:
            r = requests.get(
                src,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=64 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_RESPONSE_BYTES:
                    raise ValueError("image exceeded size cap")
            path = urlparse(src).path
            stem = _SAFE_NAME.sub("_", Path(path).stem)[:40] or "image"
            ext = _SAFE_NAME.sub("", Path(path).suffix)[:8] or ".bin"
            digest = hashlib.sha1(src.encode()).hexdigest()[:8]
            fname = f"{stem}-{digest}{ext}"
            (output_dir / fname).write_bytes(bytes(buf))
            seen[src] = fname
            img["src"] = fname
            count += 1
        except Exception as e:
            print(f"[warn] image download failed: {src}: {e}", file=sys.stderr)
    return count


MARKDOWN_TYPES = ("text/markdown", "text/x-markdown", "text/plain")
_HTML_TAG_RE = re.compile(r"<\s*(?:!doctype|html|head|body|div|p|article|main)\b", re.I)


def is_markdown_response(content_type: str, url: str, text: str) -> bool:
    """True when the body is Markdown or plain text, not HTML.

    Doc sites serve `.md`-suffixed pages as `text/markdown`; parsing that as
    HTML makes BeautifulSoup treat the whole body as one text node and
    html2text then collapses its whitespace into a single line. Decide by the
    declared type first, and by the URL suffix when the type is missing or
    generic and the body carries no HTML tag in its head.
    """
    main_type = content_type.split(";", 1)[0].strip().lower()
    if main_type in MARKDOWN_TYPES:
        return True
    if main_type.startswith("text/html") or main_type.startswith("application/xhtml"):
        return False
    path = urlparse(url).path.lower()
    return path.endswith((".md", ".markdown", ".txt")) and not _HTML_TAG_RE.search(text[:4096])


def markdown_passthrough(text: str, url: str, content_type: str) -> tuple[str, dict]:
    """The Markdown path: normalise line endings, keep every line, derive the
    title from the first ATX heading."""
    md = text.replace("\r\n", "\n").replace("\r", "\n")
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    title = None
    for line in md.splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if m:
            title = m.group(1).strip()
            break
    if not title:
        title = Path(urlparse(url).path).name or url
    word_count = len(md.split())
    metadata = {
        "title": title,
        "author": None,
        "published_date": None,
        "url": url,
        "content_type": content_type,
        "images_downloaded": 0,
        "word_count": word_count,
        "char_count": len(md),
        "thin_content": word_count < THIN_CONTENT_WORDS,
    }
    return md, metadata


def html_to_markdown(
    html: str,
    base_url: str,
    output_dir: Path | None,
    download_imgs: bool,
) -> tuple[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    metadata = _extract_metadata(soup, base_url)
    main = _extract_main(soup)
    _absolutize(main, base_url)
    if output_dir and download_imgs:
        metadata["images_downloaded"] = _download_images(main, output_dir)
    _preserve_code_languages(main)
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = False
    h.unicode_snob = True
    h.protect_links = True
    md = h.handle(str(main))
    md = _apply_code_fences(md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    word_count = len(md.split())
    metadata["word_count"] = word_count
    metadata["char_count"] = len(md)
    metadata["thin_content"] = word_count < THIN_CONTENT_WORDS
    return md, metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert a web page to Markdown for the Idyll vault."
    )
    parser.add_argument("url", help="URL to fetch (http/https only)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Save source.html, source.url, and downloaded images into this directory",
    )
    parser.add_argument(
        "--no-images", action="store_true", help="Don't download referenced images"
    )
    parser.add_argument(
        "--allow-private",
        action="store_true",
        help="Allow fetching private/loopback IPs (default: blocked)",
    )
    args = parser.parse_args()

    try:
        final_url, raw_bytes, encoding, content_type = _fetch(args.url, args.allow_private)
    except Exception as e:
        print(
            json.dumps(
                {"error": str(e), "extraction_success": False, "url": args.url},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    # Save raw bytes verbatim — preserves the actual upstream representation.
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "source.html").write_bytes(raw_bytes)
        url_file = args.url + "\n"
        if final_url != args.url:
            url_file += f"final: {final_url}\n"
        (args.output_dir / "source.url").write_text(url_file, encoding="utf-8")

    # Decode for processing using the detected encoding.
    html = raw_bytes.decode(encoding, errors="replace")

    try:
        if is_markdown_response(content_type, final_url, html):
            md, metadata = markdown_passthrough(html, final_url, content_type)
        else:
            md, metadata = html_to_markdown(
                html, final_url, args.output_dir, not args.no_images
            )
    except Exception as e:
        print(
            json.dumps(
                {"error": str(e), "extraction_success": False, "url": args.url},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    metadata["final_url"] = final_url
    metadata["extraction_success"] = True

    print(md)
    print(json.dumps(metadata, indent=2), file=sys.stderr)
    return 2 if metadata["thin_content"] else 0


if __name__ == "__main__":
    sys.exit(main())
