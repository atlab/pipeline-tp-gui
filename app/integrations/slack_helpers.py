from dataclasses import dataclass
from enum import Enum
import os
from functools import lru_cache
from typing import Optional

from flask import current_app
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackEnv(Enum):
    """Enum of Slack-related environment variable names."""
    BOT_TOKEN = "SLACK_BOT_TOKEN"
    NOTIFY_DRY_RUN = "SLACK_NOTIFY_DRY_RUN"

    SHIKIGAMI_CHANNEL = "SLACK_SHIKIGAMI_CHANNEL"
    SHIKIGAMI_MANAGER = "SLACK_SHIKIGAMI_MANAGER"
    SHIKIGAMI_MANAGER_DM = "SLACK_SHIKIGAMI_MANAGER_DM"

    SURGERY_CHANNEL = "SLACK_SURGERY_CHANNEL"
    SURGERY_MANAGER = "SLACK_SURGERY_MANAGER"
    SURGERY_MANAGER_DM = "SLACK_SURGERY_MANAGER_DM"


@dataclass(frozen=True)
class SlackEnvConfig:
    """Parsed Slack environment configuration."""
    bot_token: str
    notify_dry_run: bool

    shikigami_channel: Optional[str]
    shikigami_manager: Optional[str]
    shikigami_manager_dm: bool

    surgery_channel: Optional[str]
    surgery_manager: Optional[str]
    surgery_manager_dm: bool

    @staticmethod
    def from_env() -> "SlackEnvConfig":
        def _bool(val: Optional[str], default: bool = False) -> bool:
            if val is None:
                return default
            return str(val).strip().lower() in ("1", "true", "yes", "y", "on")

        return SlackEnvConfig(
            bot_token=os.getenv(SlackEnv.BOT_TOKEN.value, "").strip(),
            notify_dry_run=_bool(os.getenv(SlackEnv.NOTIFY_DRY_RUN.value), False),

            shikigami_channel=os.getenv(SlackEnv.SHIKIGAMI_CHANNEL.value, "").strip() or None,
            shikigami_manager=os.getenv(SlackEnv.SHIKIGAMI_MANAGER.value, "").strip() or None,
            shikigami_manager_dm=_bool(os.getenv(SlackEnv.SHIKIGAMI_MANAGER_DM.value), True),

            surgery_channel=os.getenv(SlackEnv.SURGERY_CHANNEL.value, "").strip() or None,
            surgery_manager=os.getenv(SlackEnv.SURGERY_MANAGER.value, "").strip() or None,
            surgery_manager_dm=_bool(os.getenv(SlackEnv.SURGERY_MANAGER_DM.value), True),
        )


class SlackClient:
    def __init__(self, env: Optional[SlackEnvConfig] = None, client: Optional[WebClient] = None):
        self.env = env or SlackEnvConfig.from_env()
        if not self.env.bot_token:
            current_app.logger.warning(
                f"{SlackEnv.BOT_TOKEN.value} is empty; Slack API calls will fail unless SLACK_NOTIFY_DRY_RUN=true."
            )
        self.client = client or WebClient(token=self.env.bot_token)

    @staticmethod
    def _looks_like_id(s: Optional[str]) -> bool:
        return bool(s) and s[0] in ("C", "D", "G", "U")

    def _emit_log(self, phase: str, target: str, text: str, resolved: Optional[str] = None, extra: Optional[str] = None):
        """
        phase: SENT | DRY-RUN | SKIP | ERROR | PLAN
        target: human-readable target (e.g., '#surgery-notifications', 'dm:U123')
        resolved: resolved Slack channel id (C…/D…) if any
        extra: optional notes
        """
        dr = "dry_run=true" if self.env.notify_dry_run else "dry_run=false"
        res = f" resolved={resolved}" if resolved else ""
        note = f" ({extra})" if extra else ""
        current_app.logger.info(f"[Slack {phase}] target={target}{res} {dr} text={text}{note}")

    @lru_cache(maxsize=256)
    def resolve_channel_id(self, name_or_id: Optional[str]) -> Optional[str]:
        if not name_or_id:
            return None
        if self._looks_like_id(name_or_id):
            return name_or_id
        cname = name_or_id.lstrip("#")
        try:
            cursor = None
            while True:
                resp = self.client.conversations_list(
                    limit=1000, cursor=cursor,
                    types="public_channel,private_channel"
                )
                for ch in resp.get("channels", []):
                    if ch.get("name") == cname:
                        return ch.get("id")
                cursor = resp.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break
        except SlackApiError as e:
            self._emit_log("ERROR", f"#{cname}", "(resolve_channel_id)", extra=f"{e.response.get('error') if getattr(e, 'response', None) else e}")
        return None

    @lru_cache(maxsize=256)
    def resolve_user_id(self, name_or_id: Optional[str]) -> Optional[str]:
        if not name_or_id:
            return None
        if self._looks_like_id(name_or_id) and name_or_id.startswith("U"):
            return name_or_id
        needle = name_or_id.lstrip("@")
        try:
            cursor = None
            while True:
                resp = self.client.users_list(limit=1000, cursor=cursor)
                for u in resp.get("members", []):
                    if u.get("deleted"):
                        continue
                    username = u.get("name") or ""
                    display = (u.get("profile") or {}).get("display_name") or ""
                    realname = (u.get("profile") or {}).get("real_name") or ""
                    if needle in (username, display, realname):
                        return u.get("id")
                cursor = resp.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break
        except SlackApiError as e:
            self._emit_log("ERROR", f"user:{needle}", "(resolve_user_id)", extra=f"{e.response.get('error') if getattr(e, 'response', None) else e}")
        return None

    @lru_cache(maxsize=256)
    def dm_channel_for(self, user_id: str) -> Optional[str]:
        if not user_id:
            return None
        try:
            resp = self.client.conversations_open(users=user_id)
            return (resp.get("channel") or {}).get("id")
        except SlackApiError as e:
            self._emit_log("ERROR", f"dm:{user_id}", "(conversations_open)", extra=f"{e.response.get('error') if getattr(e, 'response', None) else e}")
            return None

    def _post(self, channel: Optional[str], text: Optional[str], target_label: str) -> None:
        """
        Internal: posts to a resolved channel id (C…/D…). Logs plan/outcome in all cases.
        """
        if not channel or not text:
            self._emit_log("SKIP", target_label, text or "(empty)", resolved=channel, extra="missing channel or text")
            return
        self._emit_log("PLAN", target_label, text, resolved=channel)
        if self.env.notify_dry_run:
            self._emit_log("DRY-RUN", target_label, text, resolved=channel)
            return
        try:
            resp = self.client.chat_postMessage(channel=channel, text=text)
            ts = (resp or {}).get("ts")
            self._emit_log("SENT", target_label, text, resolved=channel, extra=(f"ts={ts}" if ts else None))
        except SlackApiError as e:
            self._emit_log("ERROR", target_label, text, resolved=channel,
                           extra=f"{e.response.get('error') if getattr(e, 'response', None) else e}")
            raise

    # --- Public helpers -----------------------------------------------------

    def send_to_shikigami_feed(self, text: str, ping_channel: bool = False) -> None:
        cfg_label = self.env.shikigami_channel or "(unset:SLACK_SHIKIGAMI_CHANNEL)"
        ch = self.resolve_channel_id(self.env.shikigami_channel) if self.env.shikigami_channel else None
        msg = f"<!channel> {text}" if ping_channel else text
        if not ch:
            self._emit_log("SKIP", cfg_label, msg, resolved=None, extra="channel not resolvable")
            return
        self._post(ch, msg, target_label=cfg_label)

    def send_to_surgery_channel(self, text: str, ping_channel: bool = False) -> None:
        cfg_label = self.env.surgery_channel or "(unset:SLACK_SURGERY_CHANNEL)"
        ch = self.resolve_channel_id(self.env.surgery_channel) if self.env.surgery_channel else None
        msg = f"<!channel> {text}" if ping_channel else text
        if not ch:
            self._emit_log("SKIP", cfg_label, msg, resolved=None, extra="channel not resolvable")
            return
        self._post(ch, msg, target_label=cfg_label)

    def _dm_user_if_enabled(self, enabled: bool, who_label: str, who_value: Optional[str], text: str) -> None:
        if not enabled:
            self._emit_log("SKIP", f"dm:{who_label}", text or "", resolved=None, extra=f"{who_label}_DM=false")
            return
        if not who_value:
            self._emit_log("SKIP", f"dm:{who_label}", text or "", resolved=None, extra=f"unset:{who_label}")
            return
        uid = self.resolve_user_id(who_value)
        if not uid:
            self._emit_log("SKIP", f"dm:{who_value}", text or "", resolved=None, extra=f"{who_label} not resolvable")
            return
        dm = self.dm_channel_for(uid)
        if not dm:
            self._emit_log("SKIP", f"dm:{uid}", text or "", resolved=None, extra="could not open DM")
            return
        self._post(dm, text, target_label=f"dm:{uid}")

    def dm_surgery_manager(self, text: str) -> None:
        self._dm_user_if_enabled(self.env.surgery_manager_dm, SlackEnv.SURGERY_MANAGER.value, self.env.surgery_manager, text)

    def dm_shikigami_manager(self, text: str) -> None:
        self._dm_user_if_enabled(self.env.shikigami_manager_dm, SlackEnv.SHIKIGAMI_MANAGER.value, self.env.shikigami_manager, text)
