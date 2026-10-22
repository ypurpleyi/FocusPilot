"""Deterministic notification policy, independent of Qt, storage and model calls."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class ReminderSettings:
    mode: str = "standard"
    work_enabled: bool = False
    work_start: str = "09:00"
    work_end: str = "18:00"
    quiet_until: str | None = None
    overdue_override: bool = True
    cooldown_minutes: int = 1

    def __post_init__(self):
        if self.mode not in ("panel", "standard", "strong"):
            raise ValueError("提醒方式无效")
        if type(self.work_enabled) is not bool or type(self.overdue_override) is not bool:
            raise ValueError("提醒开关必须为布尔值")
        for value in (self.work_start, self.work_end):
            if not isinstance(value, str) or len(value) != 5:
                raise ValueError("工作时间必须为 HH:mm")
            try:
                parsed = time.fromisoformat(value)
            except ValueError as error:
                raise ValueError("工作时间必须为 HH:mm") from error
            if parsed.isoformat(timespec="minutes") != value:
                raise ValueError("工作时间必须为 HH:mm")
        if self.work_enabled and self.work_start == self.work_end:
            raise ValueError("工作时段的开始与结束不能相同；全天提醒请取消工作时段限制")
        if type(self.cooldown_minutes) is not int or not 1 <= self.cooldown_minutes <= 60:
            raise ValueError("通知间隔必须为 1–60 分钟")
        if self.quiet_until is not None:
            try:
                parsed = datetime.fromisoformat(self.quiet_until)
            except (TypeError, ValueError) as error:
                raise ValueError("免打扰结束时间无效") from error
            if parsed.tzinfo is not None:
                raise ValueError("当前版本的免打扰结束时间必须为本地时间")


@dataclass(frozen=True)
class DeliveryPolicy:
    allowed: bool
    banner: bool = False
    reveal: bool = False
    sound: bool = False
    reason: str = ""


def quiet_reason(settings: ReminderSettings, now: datetime) -> str:
    if settings.quiet_until and now < datetime.fromisoformat(settings.quiet_until):
        return f"免打扰至 {settings.quiet_until[:16].replace('T', ' ')}"
    if settings.work_enabled:
        start = time.fromisoformat(settings.work_start)
        end = time.fromisoformat(settings.work_end)
        current = now.time()
        within = start <= current < end if start < end else current >= start or current < end
        if not within:
            return f"工作时段外（{settings.work_start}–{settings.work_end}）"
    return ""


def delivery_policy(settings: ReminderSettings, now: datetime, kind: str) -> DeliveryPolicy:
    if kind not in ("soon", "overdue"):
        raise ValueError("提醒阶段无效")
    if kind == "overdue" and settings.overdue_override:
        return DeliveryPolicy(True, True, True, True, "逾期强提醒")
    reason = quiet_reason(settings, now)
    if reason:
        return DeliveryPolicy(False, reason=reason)
    if settings.mode == "panel":
        return DeliveryPolicy(True)
    if settings.mode == "strong":
        return DeliveryPolicy(True, True, True, True)
    return DeliveryPolicy(True, True, kind == "overdue")


def cooldown_remaining(now: datetime, last_alert: datetime | None, minutes: int) -> timedelta:
    if last_alert is None or last_alert > now:
        return timedelta(0)
    return max(timedelta(0), last_alert + timedelta(minutes=minutes) - now)
