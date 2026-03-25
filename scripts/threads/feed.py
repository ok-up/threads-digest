"""Threads home feed fetcher.

Navigates to the home page via CDP and extracts the post list from the DOM.
Threads does not expose window.__INITIAL_STATE__, so we use DOM parsing +
JSON-LD / page state extraction.
"""

from __future__ import annotations

import json
import logging
import re

from .cdp import Page
from .errors import NoFeedsError
from .human import navigation_delay, sleep_random
from .types import FeedResponse, ThreadPost, ThreadsUser
from .urls import HOME_URL

logger = logging.getLogger(__name__)

# Relative time line regex: matches "21 hours", "3 minutes", "1 day", etc.
_REL_TIME_RE = re.compile(
    r"^\d+\s*("
    r"s|m|h|d|w"
    r"|sec|min|hour|day|week|month|year"
    r")s?\s*(ago)?$",
    re.IGNORECASE,
)


def _clean_content(raw: str) -> str:
    """Strip leading topic-tag lines and relative-time lines from post body.

    In the Threads DOM, span[dir=auto] mixes topic tags (e.g. "MedAesthetics")
    and relative timestamps (e.g. "21 hours") into the body text; remove them
    and everything before them.
    """
    lines = raw.split("\n")
    time_idx = next(
        (i for i, line in enumerate(lines) if _REL_TIME_RE.match(line.strip())),
        None,
    )
    if time_idx is not None:
        lines = lines[time_idx + 1:]
    return "\n".join(lines).strip()


def list_feeds(page: Page, max_posts: int = 20) -> FeedResponse:
    """Fetch the home feed, scrolling until the requested number of posts is collected.

    Args:
        page: CDP page object.
        max_posts: Maximum number of posts to return.

    Returns:
        FeedResponse containing the post list.

    Raises:
        NoFeedsError: No posts could be extracted.
    """
    logger.info("Fetching Threads home feed (max=%d)", max_posts)
    page.navigate(HOME_URL)
    page.wait_for_load(timeout=20)
    navigation_delay()

    seen_keys: set[str] = set()
    all_posts: list[ThreadPost] = []

    # Each scroll is expected to load ~5 new posts; allow extra headroom
    max_scrolls = max(15, max_posts // 4 * 3)
    stall_count = 0  # consecutive scrolls with no new posts; stop after threshold

    for scroll_i in range(max_scrolls):
        # On the first pass try the JSON path; after scrolling new posts are DOM-only
        batch = _extract_posts_from_page(page, max_posts * 3) if scroll_i == 0 \
            else _extract_from_dom(page, max_posts * 3)
        prev_len = len(all_posts)
        for p in batch:
            # URL is consistent across both JSON and DOM paths; prefer it for dedup
            key = p.url or p.content[:50]
            if key and key not in seen_keys:
                seen_keys.add(key)
                all_posts.append(p)

        new_count = len(all_posts) - prev_len
        logger.info(
            "After scroll %d: %d posts total (%d new)",
            scroll_i + 1, len(all_posts), new_count,
        )

        if len(all_posts) >= max_posts:
            break

        # 3 consecutive scrolls with no new posts means we've reached the end of the feed
        if new_count == 0:
            stall_count += 1
            if stall_count >= 3:
                logger.info("No new posts for %d consecutive scrolls, stopping", stall_count)
                break
        else:
            stall_count = 0

        # Scroll to the bottom; wait until page height grows (new posts rendered) before continuing
        prev_height = page.evaluate("document.body.scrollHeight")
        page.scroll_to_bottom()
        # Wait up to 6 seconds; break early once height increases
        for _ in range(12):
            sleep_random(400, 600)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height > prev_height:
                break

    if not all_posts:
        raise NoFeedsError()

    return FeedResponse(posts=all_posts[:max_posts])


def _extract_posts_from_page(page: Page, max_posts: int) -> list[ThreadPost]:
    """Extract post data from the current page DOM.

    Tries to read inline JSON data first (faster and more stable structure);
    falls back to DOM parsing on failure.
    """
    # Try to extract structured data from page script tags
    posts = _try_extract_from_scripts(page, max_posts)
    if posts:
        return posts

    # Fallback: DOM parsing
    return _extract_from_dom(page, max_posts)


def _try_extract_from_scripts(page: Page, max_posts: int) -> list[ThreadPost]:
    """Attempt to extract JSON data from all script tags.

    Threads SSR pages embed structured data in multiple script[type="application/json"]
    tags; iterate all of them (largest first) to locate post data.
    """
    # Collect all JSON script tags sorted by size descending (larger = more likely feed data)
    scripts_json = page.evaluate(
        """
        (() => {
            const scripts = document.querySelectorAll('script[type="application/json"]');
            const results = [];
            for (const s of scripts) {
                const text = s.textContent || '';
                // Only process scripts large enough (skip small config scripts)
                if (text.length > 500) {
                    results.push(text);
                }
            }
            // Sort descending by size so larger scripts are processed first
            results.sort((a, b) => b.length - a.length);
            return JSON.stringify(results);
        })()
        """
    )

    if not scripts_json:
        return []

    try:
        scripts = json.loads(scripts_json)
    except Exception:
        return []

    posts: list[ThreadPost] = []
    for raw in scripts:
        if len(posts) >= max_posts:
            break
        try:
            data = json.loads(raw)
            found = _parse_threads_json(data, max_posts - len(posts))
            posts.extend(found)
        except Exception as e:
            logger.debug("Failed to parse script JSON: %s", e)

    return posts[:max_posts]


def _parse_threads_json(data: dict, max_posts: int) -> list[ThreadPost]:
    """Recursively extract post data from a JSON structure.

    Threads JSON can be deeply nested; recursively search for known key patterns.
    """
    posts: list[ThreadPost] = []

    seen_ids: set[str] = set()

    def _find_posts(obj: object, depth: int = 0) -> None:
        if len(posts) >= max_posts or depth > 20:
            return
        if isinstance(obj, dict):
            # Format 1: thread_items array (unauthenticated SSR / API format)
            if "thread_items" in obj:
                for item in obj["thread_items"]:
                    if isinstance(item, dict) and "post" in item:
                        post = _parse_single_post(item["post"])
                        if post and post.post_id not in seen_ids:
                            seen_ids.add(post.post_id)
                            posts.append(post)
            # Format 2: single post object (has pk and text_post_app_info)
            elif "pk" in obj and "text_post_app_info" in obj:
                post = _parse_single_post(obj)
                if post and post.post_id not in seen_ids:
                    seen_ids.add(post.post_id)
                    posts.append(post)
            # Format 3: GraphQL Relay edges/node structure
            elif "edges" in obj and isinstance(obj["edges"], list):
                for edge in obj["edges"]:
                    if isinstance(edge, dict) and "node" in edge:
                        _find_posts(edge["node"], depth + 1)
            # Format 4: Meta Relay __bbox nesting
            elif "__bbox" in obj:
                _find_posts(obj["__bbox"], depth + 1)
            else:
                for v in obj.values():
                    _find_posts(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _find_posts(item, depth + 1)

    _find_posts(data)
    return posts[:max_posts]


def _parse_single_post(post_data: dict) -> ThreadPost | None:
    """Parse a single post object."""
    try:
        user_data = post_data.get("user", {})
        user = ThreadsUser(
            user_id=str(user_data.get("pk", user_data.get("id", ""))),
            username=user_data.get("username", ""),
            display_name=user_data.get("full_name", ""),
            avatar_url=user_data.get("profile_pic_url", ""),
            is_verified=user_data.get("is_verified", False),
        )

        # Extract body text
        caption = post_data.get("caption") or {}
        if isinstance(caption, dict):
            content = caption.get("text", "")
        else:
            content = str(caption) if caption else ""

        # Extract engagement counts
        like_info = post_data.get("like_and_view_counts_disabled", False)
        like_count = "" if like_info else str(post_data.get("like_count", ""))
        reply_count = str(
            post_data.get("text_post_app_info", {}).get("direct_reply_count", "")
        )

        # Extract images
        images: list[str] = []
        carousel = post_data.get("carousel_media") or []
        for media in carousel:
            if isinstance(media, dict):
                candidates = media.get("image_versions2", {}).get("candidates", [])
                if candidates:
                    images.append(candidates[0].get("url", ""))

        if not carousel:
            candidates = (
                post_data.get("image_versions2", {}).get("candidates", [])
            )
            if candidates:
                images.append(candidates[0].get("url", ""))

        post_id = str(post_data.get("pk", post_data.get("id", "")))
        code = post_data.get("code", "")
        url = f"https://www.threads.com/@{user.username}/post/{code or post_id}"
        # If pk is missing, use the shortcode as postId (home feed JSON has code but no pk)
        if not post_id:
            post_id = code

        return ThreadPost(
            post_id=post_id,
            author=user,
            content=content,
            like_count=like_count,
            reply_count=reply_count,
            created_at=str(post_data.get("taken_at", "")),
            images=images,
            url=url,
        )
    except Exception as e:
        logger.debug("Failed to parse post: %s", e)
        return None


def _extract_from_dom(page: Page, max_posts: int) -> list[ThreadPost]:
    """Extract posts from the DOM (fallback path).

    Correctly separates body text, timestamps, and like/reply/quote/repost counts.
    Based on verified selector: div[data-pressable-container="true"]
    """
    # Inject max_posts via a separate evaluate to avoid string concatenation
    page.evaluate(f"window.__THREADS_MAX_POSTS = {int(max_posts)};")
    posts_data = page.evaluate(
        """
        (() => {
            const maxPosts = window.__THREADS_MAX_POSTS || 20;
            const results = [];
            const containers = document.querySelectorAll('div[data-pressable-container="true"]');
            for (const container of containers) {
                if (results.length >= maxPosts) break;
                try {
                    // Author username
                    const authorLink = container.querySelector('a[href^="/@"]');
                    const usernameHref = authorLink?.getAttribute('href') || '';
                    const username = usernameHref.replace('/@', '').split('/')[0] || '';

                    // Timestamp (datetime attr is ISO; textContent is relative)
                    const timeEl = container.querySelector('time');
                    const datetime = timeEl?.getAttribute('datetime') || '';
                    const timeText = timeEl?.textContent?.trim() || '';

                    // Post body: span[dir="auto"] not inside author link or time
                    const allSpans = container.querySelectorAll('span[dir="auto"]');
                    const contentSpans = Array.from(allSpans).filter(span => {
                        if (authorLink && authorLink.contains(span)) return false;
                        if (timeEl && timeEl.contains(span)) return false;
                        // Exclude spans inside interaction buttons (like count, reply count, etc.)
                        if (span.closest('[role="button"]')) return false;
                        return true;
                    });
                    const content = contentSpans
                        .map(s => s.textContent?.trim())
                        .filter(Boolean)
                        .join('\\n');

                    // Engagement counts from role=button elements
                    let likeCount = '', replyCount = '', repostCount = '', quoteCount = '';
                    const btns = container.querySelectorAll('[role="button"]');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        // Check aria-label first (more reliable), then text prefixes
                        const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                        if (!likeCount && label.includes('like'))
                            likeCount = t.replace(/^likes?\\s*/i, '').trim();
                        else if (!replyCount && label.includes('repl'))
                            replyCount = t.replace(/^repl\\w*\\s*/i, '').trim();
                        else if (!repostCount && label.includes('repost'))
                            repostCount = t.replace(/^reposts?\\s*/i, '').trim();
                        else if (!quoteCount && label.includes('share'))
                            quoteCount = t.replace(/^shares?\\s*/i, '').trim();
                    }

                    // Post link
                    const postLink = container.querySelector('a[href*="/post/"]');
                    const postHref = postLink?.getAttribute('href') || '';
                    const url = postHref ? 'https://www.threads.com' + postHref : '';

                    if (content || username) {
                        results.push({
                            username, content,
                            datetime, timeText,
                            likeCount, replyCount, repostCount, quoteCount,
                            url,
                        });
                    }
                } catch(e) {}
            }
            return JSON.stringify(results);
        })()
        """
    )

    if not posts_data:
        return []

    try:
        items = json.loads(posts_data)
        posts = []
        for item in items:
            if not (item.get("content") or item.get("username")):
                continue
            url = item.get("url", "")
            # Extract shortcode from URL as postId (e.g. /post/DVxHauYk1YQ)
            post_id = url.split("/post/")[-1].strip("/") if "/post/" in url else ""
            posts.append(ThreadPost(
                post_id=post_id,
                author=ThreadsUser(username=item.get("username", "")),
                content=_clean_content(item.get("content", "")),
                like_count=item.get("likeCount", ""),
                reply_count=item.get("replyCount", ""),
                repost_count=item.get("repostCount", ""),
                quote_count=item.get("quoteCount", ""),
                created_at=item.get("datetime") or item.get("timeText", ""),
                url=url,
            ))
        return posts
    except Exception:
        return []
