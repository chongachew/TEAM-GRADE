"""
YouTube Metadata Extraction Module
Fetches and parses YouTube video metadata for college football games.
"""

import re
import secrets
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs
from datetime import datetime

logger = logging.getLogger(__name__)


class YouTubeMetadataExtractor:
    """Extract and parse metadata from YouTube URLs and videos."""

    # Regex patterns for football metadata
    PATTERNS = {
        "teams": r"(\w+(?:\s+\w+)?)\s+(?:vs\.?|vs|@)\s+(\w+(?:\s+\w+)?)",
        "year": r"(20\d{2}|2\d{3})",
        "cfp_round": r"(CFP|College Football Playoff)\s+(?:Final|Semifinal|Championship|Elite Eight|Round of (?:16|12|8))",
        "level": r"(FCS|FBS|Division I|D1|College|High School|HS|NFL|NFL Draft)",
        "sport": r"(high school|college|nfl|pro|professional)",
    }

    # Common team name variations
    TEAM_ALIASES = {
        "ala": "Alabama",
        "lsu": "LSU",
        "texas": "Texas",
        "ohio st": "Ohio State",
        "georgia": "Georgia",
        "oklahoma": "Oklahoma",
        "clemson": "Clemson",
        "fl": "Florida",
        "bama": "Alabama",
        "ttu": "Texas Tech",
        "osu": "Ohio State",
        "ok state": "Oklahoma State",
        "u of a": "Arizona",
        "u of m": "Michigan",
    }

    # Additional video sources yt-dlp already supports natively (blueprint's
    # "Additional video sources" section). Instagram and Hudl are deliberately
    # excluded - Instagram's extractor is flaky/high-maintenance, and Hudl has
    # no scrapable stream at all (the direct-file-upload endpoint is the
    # intended path for a Hudl export instead).
    NON_YOUTUBE_HOST_PATTERNS = [
        r"(?:https?://)?(?:www\.)?vimeo\.com",
        r"(?:https?://)?(?:www\.|vm\.)?tiktok\.com",
        r"(?:https?://)?drive\.google\.com",
    ]
    DIRECT_FILE_EXTENSIONS = (".mp4", ".m3u8", ".mov", ".webm")

    @staticmethod
    def get_video_id(url: str) -> Optional[str]:
        """
        Resolve a submitted URL to TEAM-GRADE's internal 11-char video ID.

        For YouTube URLs (or a bare 11-char ID), returns the real YouTube
        video ID, preserving today's re-submission/dedup behavior. For every
        other source this extractor recognizes as valid (see
        validate_youtube_url - Vimeo, TikTok, Google Drive shares, direct
        video-file URLs), synthesizes a random 11-char ID using the same
        scheme the direct-upload endpoint already uses (api/server.py's
        `secrets.token_urlsafe(9)[:11]`), since those sources' own IDs aren't
        naturally 11 chars of [A-Za-z0-9_-] and every downstream pipeline
        stage (VideoIdValidator) requires that exact shape.

        Supports formats:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - VIDEO_ID (raw ID)
        - Vimeo / TikTok / Google Drive / direct .mp4|.m3u8|.mov|.webm URLs

        Args:
            url: submitted video URL or a raw video ID

        Returns:
            11-char internal video ID, or None if the URL isn't recognized
        """
        # Check if it's already a video ID
        if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
            return url

        try:
            parsed = urlparse(url)
            is_youtube_host = bool(parsed.hostname) and (
                "youtube.com" in parsed.hostname or "youtu.be" in parsed.hostname
            )

            if is_youtube_host or "/embed/" in url:
                # Format: watch?v=VIDEO_ID
                if parsed.hostname and "youtube.com" in parsed.hostname:
                    query = parse_qs(parsed.query)
                    if "v" in query:
                        return query["v"][0]

                # Format: youtu.be/VIDEO_ID
                elif parsed.hostname and "youtu.be" in parsed.hostname:
                    video_id = parsed.path.lstrip("/")
                    if video_id:
                        return video_id

                # Format: youtube.com/embed/VIDEO_ID
                if "/embed/" in url:
                    match = re.search(r"/embed/([a-zA-Z0-9_-]{11})", url)
                    if match:
                        return match.group(1)

                return None  # looked like YouTube but no ID could be extracted

        except Exception as e:
            logger.error(f"Error parsing video URL: {e}")
            return None

        # Not YouTube - synthesize an internal ID for any other source this
        # extractor recognizes as valid.
        if YouTubeMetadataExtractor.validate_youtube_url(url):
            return secrets.token_urlsafe(9)[:11]

        return None

    @staticmethod
    def validate_youtube_url(url: str) -> bool:
        """
        Validate whether a submitted URL is from a source TEAM-GRADE accepts.

        Despite the name (kept as-is to avoid rippling a rename through the
        ~10 call/export sites in api/server.py, ingest_worker.py, and
        ingest/__init__.py), this recognizes every source in the blueprint's
        "Additional video sources" section - YouTube, Vimeo, TikTok, Google
        Drive shares, and direct video-file URLs, all of which yt-dlp already
        supports natively.

        Args:
            url: URL to validate

        Returns:
            True if the URL is from a supported source
        """
        youtube_patterns = [
            r"(?:https?://)?(?:www\.)?youtube\.com",
            r"(?:https?://)?youtu\.be",
            r"^[a-zA-Z0-9_-]{11}$",  # Raw video ID
        ]

        for pattern in youtube_patterns:
            if re.search(pattern, url):
                return True

        for pattern in YouTubeMetadataExtractor.NON_YOUTUBE_HOST_PATTERNS:
            if re.search(pattern, url):
                return True

        try:
            path = urlparse(url).path.lower()
        except Exception:
            return False

        return path.endswith(YouTubeMetadataExtractor.DIRECT_FILE_EXTENSIONS)

    @staticmethod
    def normalize_team_name(team: str) -> str:
        """
        Normalize team name variations.

        Args:
            team: Raw team name from metadata

        Returns:
            Normalized team name
        """
        team_lower = team.strip().lower()

        # Check aliases
        for alias, normalized in YouTubeMetadataExtractor.TEAM_ALIASES.items():
            if alias in team_lower:
                return normalized

        # Return title-cased original if no alias found
        return team.strip().title()

    @staticmethod
    def extract_teams(title: str, description: str = "") -> Tuple[Optional[str], Optional[str]]:
        """
        Extract team names from video title and description.

        Args:
            title: Video title
            description: Video description

        Returns:
            Tuple of (team1, team2) or (None, None)
        """
        full_text = f"{title} {description}".upper()

        match = re.search(
            YouTubeMetadataExtractor.PATTERNS["teams"],
            full_text,
            re.IGNORECASE
        )

        if match:
            team1 = YouTubeMetadataExtractor.normalize_team_name(match.group(1))
            team2 = YouTubeMetadataExtractor.normalize_team_name(match.group(2))
            return team1, team2

        return None, None

    @staticmethod
    def extract_year(title: str, description: str = "") -> Optional[int]:
        """
        Extract year/season from metadata.

        Args:
            title: Video title
            description: Video description

        Returns:
            Year as integer or None
        """
        full_text = f"{title} {description}"

        match = re.search(YouTubeMetadataExtractor.PATTERNS["year"], full_text)
        if match:
            try:
                year = int(match.group(1))
                if 2000 <= year <= datetime.now().year + 1:
                    return year
            except ValueError:
                pass

        return None

    @staticmethod
    def extract_cfp_round(title: str, description: str = "") -> Optional[str]:
        """
        Extract CFP (College Football Playoff) round information.

        Args:
            title: Video title
            description: Video description

        Returns:
            CFP round identifier or None
        """
        full_text = f"{title} {description}".upper()

        match = re.search(
            YouTubeMetadataExtractor.PATTERNS["cfp_round"],
            full_text,
            re.IGNORECASE
        )

        if match:
            round_text = match.group(0).upper()

            if "CHAMPIONSHIP" in round_text or "FINAL" in round_text:
                return "CFP_CHAMPIONSHIP"
            elif "SEMIFINAL" in round_text:
                return "CFP_SEMIFINAL"
            elif "ELITE" in round_text and "EIGHT" in round_text:
                return "CFP_ELITE_EIGHT"
            elif "ROUND" in round_text:
                if "16" in round_text:
                    return "CFP_ROUND_OF_16"
                elif "12" in round_text:
                    return "CFP_ROUND_OF_12"
                elif "8" in round_text:
                    return "CFP_ROUND_OF_8"

            return "CFP_OTHER"

        return None

    @staticmethod
    def extract_level(title: str, description: str = "") -> str:
        """
        Extract player/game level (college, high school, NFL, etc.).

        Args:
            title: Video title
            description: Video description

        Returns:
            Level string (default: "college")
        """
        full_text = f"{title} {description}".lower()

        # Check for specific levels
        if any(term in full_text for term in ["nfl", "pro", "professional", "national football league"]):
            return "nfl"
        elif any(term in full_text for term in ["high school", "hs ", "prep"]):
            return "high_school"
        elif any(term in full_text for term in ["college", "ncaa", "fbs", "fcs", "d1", "division i"]):
            return "college"

        # Default to college for football
        return "college"

    @staticmethod
    def parse_football_metadata(
        title: str,
        description: str = "",
        video_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse full football metadata from video title and description.

        Args:
            title: Video title
            description: Video description
            video_id: YouTube video ID (optional)

        Returns:
            Dictionary with parsed metadata
        """
        logger.info(f"Parsing metadata for title: {title[:80]}...")

        team1, team2 = YouTubeMetadataExtractor.extract_teams(title, description)
        year = YouTubeMetadataExtractor.extract_year(title, description)
        cfp_round = YouTubeMetadataExtractor.extract_cfp_round(title, description)
        level = YouTubeMetadataExtractor.extract_level(title, description)

        metadata = {
            "video_id": video_id or "",
            "title": title,
            "description": description[:500],  # Truncate long descriptions
            "team1": team1,
            "team2": team2,
            "year": year,
            "level": level,
            "cfp_round": cfp_round,
            "parsed_at": datetime.utcnow().isoformat(),
            "confidence": {
                "teams": 0.8 if (team1 and team2) else 0.0,
                "year": 0.9 if year else 0.0,
                "level": 0.8,
                "cfp_round": 0.7 if cfp_round else 0.0,
            }
        }

        logger.info(f"Parsed metadata: Teams={team1} vs {team2}, Year={year}, Level={level}")

        return metadata

    @staticmethod
    def fetch_youtube_metadata(video_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata from YouTube API.

        NOTE: This requires yt-dlp or pytube. For now, returns placeholder.
        In production, integrate with YouTube Data API v3 or yt-dlp.

        Args:
            video_id: YouTube video ID

        Returns:
            Dictionary with video metadata or None if fetch fails
        """
        try:
            import yt_dlp

            logger.info(f"Fetching metadata for video: {video_id}")

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }
            from config import settings
            if settings.YOUTUBE_COOKIES_FILE:
                ydl_opts["cookiefile"] = settings.YOUTUBE_COOKIES_FILE

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

                metadata = {
                    "video_id": video_id,
                    "title": info.get("title", ""),
                    "description": info.get("description", ""),
                    "duration": info.get("duration", 0),
                    "upload_date": info.get("upload_date", ""),
                    "uploader": info.get("uploader", ""),
                    "view_count": info.get("view_count", 0),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                }

                logger.info(f"[OK] Fetched metadata: {metadata['title']}")
                return metadata

        except ImportError:
            logger.warning("yt-dlp not installed. Returning basic metadata.")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch YouTube metadata: {e}")
            return None


def get_video_id(url: str) -> Optional[str]:
    """Standalone function to get video ID."""
    return YouTubeMetadataExtractor.get_video_id(url)


def fetch_youtube_metadata(video_id: str) -> Optional[Dict[str, Any]]:
    """Standalone function to fetch metadata."""
    return YouTubeMetadataExtractor.fetch_youtube_metadata(video_id)


def parse_football_metadata(
    title: str,
    description: str = "",
    video_id: Optional[str] = None
) -> Dict[str, Any]:
    """Standalone function to parse metadata."""
    return YouTubeMetadataExtractor.parse_football_metadata(title, description, video_id)


def validate_youtube_url(url: str) -> bool:
    """Standalone function to validate URL."""
    return YouTubeMetadataExtractor.validate_youtube_url(url)
