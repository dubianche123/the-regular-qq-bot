"""天气联动：维护者在控制台（或和她聊天时）选一个城市，桌宠按那里的实际天气挑应景的小动作。

只向 Open-Meteo（免费、不需要密钥）发两种请求：选城市时查一次地名，之后最多每 30 分钟查一次那个
经纬度的当前天气。请求里只有城市名或经纬度，没有群聊、身份或本机信息；默认直连、不重试，查不到就
当作没有天气，桌宠照常过日子。设置和最近一次读数存在实例的 data/weather.json，不进仓库。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
FILENAME = "weather.json"
REFRESH_SECONDS = 1800.0
RETRY_SECONDS = 300.0          # a failed fetch waits this long before the next try (no tight retry loop)
TIMEOUT_SECONDS = 8.0
MAX_CITY_CHARS = 40
HOT_FEELS = 30.0               # 体感到这里算热：瘫一会儿、冰一杯可乐
COLD_FEELS = 5.0               # 体感到这里算冷：呵火暖手、烘围巾、烤棉花糖
SUN_FEELS = 12.0               # 晴天、白天、体感不冷才晒太阳

# WMO weather interpretation codes, folded into the few skies she reacts to.
_SKY = {0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy", 45: "fog", 48: "fog",
        **{code: "rain" for code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82)},
        **{code: "snow" for code in (71, 73, 75, 77, 85, 86)},
        **{code: "storm" for code in (95, 96, 99)}}
SKY_LABELS = {"clear": "晴", "cloudy": "多云", "fog": "雾", "rain": "雨", "snow": "雪", "storm": "雷雨"}


def _path(cfg):
    return Path(cfg.DATA_DIR) / FILENAME


def load(cfg) -> dict:
    try:
        data = json.loads(_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cfg, data):
    path = _path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def city_text(value) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError("请填一个城市名。")
    if len(text) > MAX_CITY_CHARS:
        raise ValueError(f"城市名最多 {MAX_CITY_CHARS} 个字。")
    return text


def place_label(place) -> str:
    if not place:
        return ""
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    seen = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return " · ".join(seen)


def save_place(cfg, place) -> dict:
    data = {"place": place, "reading": None, "fetched_at": 0.0, "failed_at": 0.0}
    _write(cfg, data)
    return data


def clear(cfg):
    _write(cfg, {})


def classify(current) -> dict:
    """Open-Meteo `current` block -> the small reading the pet uses."""
    code = int(current.get("weather_code", -1))
    temp = float(current["temperature_2m"])
    feels = float(current.get("apparent_temperature", temp))
    return {"sky": _SKY.get(code, "cloudy"), "temp": round(temp, 1), "feels": round(feels, 1),
            "is_day": bool(current.get("is_day", 1)), "wind": round(float(current.get("wind_speed_10m", 0.0)), 1)}


def scene(reading) -> str | None:
    """Which weather vignette fits right now: rain, snow, hot, cold, sun, or None for an ordinary day."""
    if not reading:
        return None
    sky, feels = reading.get("sky"), reading.get("feels", 20.0)
    if sky in ("rain", "storm"):
        return "rain"
    if sky == "snow":
        return "snow"
    if feels >= HOT_FEELS:
        return "hot"
    if feels <= COLD_FEELS:
        return "cold"
    if sky == "clear" and reading.get("is_day") and feels >= SUN_FEELS:
        return "sun"
    return None


def describe(data) -> str:
    """One line for the panel and the steward: where, and what it is like there now."""
    place, reading = data.get("place"), data.get("reading")
    if not place:
        return "还没选城市"
    if not reading:
        return place_label(place) + "：还没查到天气"
    return (f"{place_label(place)}：{SKY_LABELS.get(reading['sky'], reading['sky'])}，"
            f"{reading['temp']:g}°C（体感 {reading['feels']:g}°C）")


def snapshot(cfg, now=None) -> dict | None:
    """What the pet gets in presence: the reading only while it is fresh enough to act on."""
    now = time.time() if now is None else now
    data = load(cfg)
    reading = data.get("reading")
    if not data.get("place") or not reading or now - float(data.get("fetched_at") or 0) > 3 * REFRESH_SECONDS:
        return None
    return {**reading, "scene": scene(reading)}


def due(cfg, now=None) -> bool:
    now = time.time() if now is None else now
    data = load(cfg)
    fetched, failed = float(data.get("fetched_at") or 0), float(data.get("failed_at") or 0)
    return (bool(data.get("place")) and (not fetched or now - fetched >= REFRESH_SECONDS)
            and (not failed or now - failed >= RETRY_SECONDS))


async def geocode(session, city):
    """Best match for a city name, or None. One request, no retry."""
    import aiohttp

    params = {"name": city_text(city), "count": 1, "language": "zh", "format": "json"}
    async with session.get(GEOCODE, params=params,
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as response:
        if response.status != 200:
            return None
        results = (await response.json()).get("results") or []
    if not results:
        return None
    hit = results[0]
    return {"name": str(hit.get("name") or city), "admin1": str(hit.get("admin1") or ""),
            "country": str(hit.get("country") or ""), "latitude": round(float(hit["latitude"]), 4),
            "longitude": round(float(hit["longitude"]), 4)}


async def refresh(cfg, session, now=None):
    """Fetch the current weather for the saved place; a failure keeps the old reading and backs off."""
    import aiohttp

    now = time.time() if now is None else now
    data = load(cfg)
    place = data.get("place")
    if not place:
        return None
    params = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
              "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m"}
    try:
        async with session.get(FORECAST, params=params,
                               timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)) as response:
            if response.status != 200:
                raise ValueError(response.status)
            reading = classify((await response.json())["current"])
    except Exception:
        _write(cfg, {**data, "failed_at": now})
        return None
    latest = load(cfg)
    if latest.get("place") != place:      # the city changed while this request was in flight
        return None
    _write(cfg, {**latest, "reading": reading, "fetched_at": now, "failed_at": 0.0})
    return reading
