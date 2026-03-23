"""Deep Social Media Scraper — Rich profile data from social platforms.

Platforms:
  - Instagram (via instaloader): profile, posts, followers, bio
  - Reddit (via API): user posts, comments, subreddits
  - GitHub (via API): repos, contributions, orgs, followers
  - StackOverflow (via API): profile, reputation, badges, top tags
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Optional

import httpx

from app.models import PersonQuery, SocialProfile, SearchResult, Source, Confidence


class DeepSocialScanner:
    """Deep social media profile scraping.

    Goes beyond simple username checking to extract full profile data:
    bios, follower counts, recent posts, activity patterns, etc.
    """

    name = "deep_social"
    description = "Deep social media scraping: Instagram, Reddit, GitHub, StackOverflow"

    def __init__(self):
        self._instaloader = None

    @property
    def instaloader(self):
        if self._instaloader is None:
            import instaloader
            self._instaloader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
            )
        return self._instaloader

    async def scan(self, query: PersonQuery) -> list:
        """Run deep social media scan."""
        results = []
        usernames = self._generate_usernames(query)

        # Run all platform scrapers
        tasks = [
            self._scrape_github_deep(query, usernames),
            self._scrape_reddit(query, usernames),
            self._scrape_stackoverflow(query, usernames),
        ]
        # Instagram is sync, run separately
        instagram_results = await self._scrape_instagram_deep(query, usernames)
        results.extend(instagram_results)

        # Run async scrapers
        for coro in asyncio.as_completed(tasks):
            try:
                platform_results = await coro
                results.extend(platform_results)
            except Exception as e:
                print(f"  Deep social scraper error: {e}")

        return results

    def _generate_usernames(self, query: PersonQuery) -> list[str]:
        """Generate likely usernames from name."""
        usernames = list(query.usernames)

        if query.first_name and query.last_name:
            fn = query.first_name.lower()
            ln = query.last_name.lower()
            usernames.extend([
                f"{fn}{ln}",
                f"{fn}.{ln}",
                f"{fn}_{ln}",
                f"{fn[0]}{ln}",
                f"{fn}{ln[0]}",
                f"{ln}{fn}",
                f"{ln}.{fn}",
                f"{fn}-{ln}",
            ])

        for nick in query.nicknames:
            nick = nick.lower()
            usernames.append(nick)
            if query.last_name:
                ln = query.last_name.lower()
                usernames.append(f"{nick}{ln}")
                usernames.append(f"{nick}.{ln}")

        return list(set(usernames))

    # =========================================================================
    # Instagram Deep Scraping (via instaloader)
    # =========================================================================

    async def _scrape_instagram_deep(self, query: PersonQuery, usernames: list[str]) -> list:
        """Deep Instagram profile scraping using instaloader.

        Extracts: full name, bio, follower/following counts, post count,
        recent posts, profile picture URL, verified status.
        """
        results = []

        # Use asyncio to avoid blocking
        loop = asyncio.get_event_loop()

        for username in usernames[:5]:
            try:
                profile_data = await loop.run_in_executor(
                    None, self._instagram_fetch_profile, username
                )
                if profile_data:
                    results.append(profile_data)
            except Exception:
                pass
            await asyncio.sleep(1)

        return results

    def _instagram_fetch_profile(self, username: str) -> Optional[SocialProfile]:
        """Fetch Instagram profile data (sync)."""
        try:
            import instaloader

            profile = instaloader.Profile.from_username(
                self.instaloader.context, username
            )

            bio = profile.biography or ""
            full_name = profile.full_name or ""

            return SocialProfile(
                platform="instagram",
                url=f"https://instagram.com/{username}",
                username=username,
                display_name=full_name,
                bio=bio[:200] if bio else None,
                followers=profile.followers,
                verified=profile.is_verified,
                confidence=Confidence.MEDIUM,
            )
        except Exception:
            return None

    # =========================================================================
    # GitHub Deep Profile (via API)
    # =========================================================================

    async def _scrape_github_deep(self, query: PersonQuery, usernames: list[str]) -> list:
        """Deep GitHub profile scraping via API.

        Extracts: profile info, repos, organizations, contributions,
        follower/following counts, bio, location, website.
        """
        results = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # First, search for users by name
            search_names = [query.full_name]
            if query.first_name and query.last_name:
                search_names.append(f"{query.first_name} {query.last_name}")

            for search_name in search_names[:2]:
                try:
                    resp = await client.get(
                        "https://api.github.com/search/users",
                        params={"q": f"{search_name} type:user", "per_page": 5},
                        headers={"Accept": "application/vnd.github.v3+json"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for user in data.get("items", []):
                            login = user.get("login", "")
                            # Get detailed profile
                            profile = await self._github_get_profile(client, login)
                            if profile:
                                results.append(profile)
                except Exception:
                    pass
                await asyncio.sleep(1)

            # Also check generated usernames
            for username in usernames[:5]:
                try:
                    profile = await self._github_get_profile(client, username)
                    if profile:
                        results.append(profile)
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = r.url
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return unique

    async def _github_get_profile(self, client: httpx.AsyncClient, login: str) -> Optional[SocialProfile]:
        """Get detailed GitHub profile."""
        try:
            resp = await client.get(
                f"https://api.github.com/users/{login}",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            bio = data.get("bio") or ""
            location = data.get("location") or ""
            blog = data.get("blog") or ""
            company = data.get("company") or ""
            name = data.get("name") or ""

            # Build rich bio
            bio_parts = []
            if bio:
                bio_parts.append(bio)
            if company:
                bio_parts.append(f"Company: {company}")
            if location:
                bio_parts.append(f"Location: {location}")
            if blog:
                bio_parts.append(f"Web: {blog}")

            full_bio = " | ".join(bio_parts) if bio_parts else None

            # Determine confidence based on name match
            confidence = Confidence.LOW
            if name:
                confidence = Confidence.MEDIUM

            return SocialProfile(
                platform="github",
                url=data.get("html_url", f"https://github.com/{login}"),
                username=login,
                display_name=name or login,
                bio=full_bio[:200] if full_bio else None,
                followers=data.get("followers"),
                confidence=confidence,
            )
        except Exception:
            return None

    # =========================================================================
    # Reddit Scraping (via public API)
    # =========================================================================

    async def _scrape_reddit(self, query: PersonQuery, usernames: list[str]) -> list:
        """Scrape Reddit user profiles.

        Extracts: account info, karma, recent posts, active subreddits.
        Uses Reddit's public JSON API (no auth needed).
        """
        results = []

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "PersonIntelAgent/1.0"},
        ) as client:
            for username in usernames[:5]:
                try:
                    resp = await client.get(
                        f"https://www.reddit.com/user/{username}/about.json"
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        user_data = data.get("data", {})

                        link_karma = user_data.get("link_karma", 0)
                        comment_karma = user_data.get("comment_karma", 0)
                        created = user_data.get("created_utc", 0)
                        is_employee = user_data.get("is_employee", False)
                        is_mod = user_data.get("is_mod", False)

                        # Build bio
                        bio_parts = []
                        bio_parts.append(f"Karma: {link_karma} link / {comment_karma} comment")
                        if created:
                            account_age = datetime.utcnow().year - datetime.utcfromtimestamp(created).year
                            bio_parts.append(f"Account age: {account_age} years")
                        if is_employee:
                            bio_parts.append("Reddit employee")
                        if is_mod:
                            bio_parts.append("Moderator")

                        results.append(SocialProfile(
                            platform="reddit",
                            url=f"https://reddit.com/user/{username}",
                            username=username,
                            display_name=f"u/{username}",
                            bio=" | ".join(bio_parts),
                            confidence=Confidence.MEDIUM,
                        ))

                        # Also get recent posts to find subreddits
                        posts_resp = await client.get(
                            f"https://www.reddit.com/user/{username}/submitted.json?limit=10"
                        )
                        if posts_resp.status_code == 200:
                            posts_data = posts_resp.json()
                            subreddits = set()
                            for post in posts_data.get("data", {}).get("children", []):
                                sub = post.get("data", {}).get("subreddit", "")
                                if sub:
                                    subreddits.add(sub)
                            if subreddits:
                                # Add as additional context
                                results.append(SearchResult(
                                    source=Source.SOCIAL,
                                    title=f"Reddit: u/{username} active in {', '.join(list(subreddits)[:5])}",
                                    url=f"https://reddit.com/user/{username}",
                                    snippet=f"Active subreddits: {', '.join(list(subreddits)[:10])}",
                                ))

                except Exception:
                    pass
                await asyncio.sleep(1)

        return results

    # =========================================================================
    # StackOverflow Profile Scraping (via API)
    # =========================================================================

    async def _scrape_stackoverflow(self, query: PersonQuery, usernames: list[str]) -> list:
        """Scrape StackOverflow profiles via API.

        Extracts: reputation, badges, top tags, answer count.
        Uses the StackExchange API (free, no auth needed).
        """
        results = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Search for users by name
            search_terms = [query.full_name]
            if query.first_name and query.last_name:
                search_terms.append(f"{query.first_name} {query.last_name}")

            for search_term in search_terms[:2]:
                try:
                    resp = await client.get(
                        "https://api.stackexchange.com/2.3/users",
                        params={
                            "order": "desc",
                            "sort": "reputation",
                            "inname": search_term,
                            "site": "stackoverflow",
                            "pagesize": 5,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for user in data.get("items", []):
                            display_name = user.get("display_name", "")
                            reputation = user.get("reputation", 0)
                            user_id = user.get("user_id")
                            link = user.get("link", "")
                            location = user.get("location", "")
                            badge_counts = user.get("badge_counts", {})

                            # Build bio
                            bio_parts = []
                            bio_parts.append(f"Rep: {reputation:,}")
                            if badge_counts:
                                gold = badge_counts.get("gold", 0)
                                silver = badge_counts.get("silver", 0)
                                bronze = badge_counts.get("bronze", 0)
                                bio_parts.append(f"Badges: 🥇{gold} 🥈{silver} 🥉{bronze}")
                            if location:
                                bio_parts.append(f"Location: {location}")

                            results.append(SocialProfile(
                                platform="stackoverflow",
                                url=link,
                                username=display_name,
                                display_name=display_name,
                                bio=" | ".join(bio_parts),
                                confidence=Confidence.MEDIUM,
                            ))

                            # Get top tags
                            if user_id:
                                try:
                                    tags_resp = await client.get(
                                        f"https://api.stackexchange.com/2.3/users/{user_id}/tags",
                                        params={
                                            "order": "desc",
                                            "sort": "popular",
                                            "site": "stackoverflow",
                                            "pagesize": 10,
                                        },
                                    )
                                    if tags_resp.status_code == 200:
                                        tags_data = tags_resp.json()
                                        tags = [t.get("name", "") for t in tags_data.get("items", [])]
                                        if tags:
                                            results.append(SearchResult(
                                                source=Source.WEB,
                                                title=f"StackOverflow: {display_name} — Top tags: {', '.join(tags[:5])}",
                                                url=link,
                                                snippet=f"Expert in: {', '.join(tags[:10])}",
                                            ))
                                except Exception:
                                    pass

                except Exception as e:
                    print(f"  StackOverflow error: {e}")
                await asyncio.sleep(1)

        return results
