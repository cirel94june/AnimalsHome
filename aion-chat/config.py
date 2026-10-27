"""
全局配置：路径、常量、settings / worldbook / chat_status 读写
"""

import re
import atexit, json, os, re, shutil, sys, tempfile, time
from pathlib import Path

# ── 路径 ─────────────────────────────────────────
BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR.parent / "public"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _is_test_process() -> bool:
    entrypoint = Path(str(sys.argv[0] or "")).name.lower()
    return (
        "unittest" in sys.modules
        or "pytest" in sys.modules
        or entrypoint.startswith("test_")
        or "pytest" in entrypoint
        or "unittest" in entrypoint
    )


if _is_test_process():
    _TEST_DB_DIR = Path(tempfile.mkdtemp(prefix=f"aionshome-tests-{os.getpid()}-"))
    atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)
    DB_PATH = _TEST_DB_DIR / "chat.db"
else:
    DB_PATH = DATA_DIR / "chat.db"
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
ALBUM_DIR = DATA_DIR / "album"
ALBUM_IMAGES_DIR = ALBUM_DIR / "images"
ALBUM_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
SONGS_DIR = DATA_DIR / "songs"
SONGS_DIR.mkdir(exist_ok=True)
CODEX_UPLOADS_DIR = BASE_DIR.parent / "Connor-Codex" / "uploads"
CODEX_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
CHATS_DIR = DATA_DIR / "chats"
CHATS_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)
MONITOR_LOGS_DIR = DATA_DIR / "monitor_logs"
MONITOR_LOGS_DIR.mkdir(exist_ok=True)
TTS_CACHE_DIR = DATA_DIR / "tts_cache"
TTS_CACHE_DIR.mkdir(exist_ok=True)
TTS_CACHE_MAX_BYTES = 500 * 1024 * 1024
THEATER_TTS_CACHE_DIR = DATA_DIR / "theater_tts_cache"
THEATER_TTS_CACHE_DIR.mkdir(exist_ok=True)
THEATER_TTS_SEGMENT_DELETE_DELAY_SECONDS = 2 * 60 * 60
DIARY_TTS_CACHE_DIR = DATA_DIR / "diary_tts_cache"
DIARY_TTS_CACHE_DIR.mkdir(exist_ok=True)

SETTINGS_PATH = DATA_DIR / "settings.json"
WORLDBOOK_PATH = DATA_DIR / "worldbook.json"
CHAT_STATUS_PATH = DATA_DIR / "chat_status.json"
CAM_CONFIG_PATH = DATA_DIR / "cam_config.json"
DIGEST_ANCHOR_PATH = DATA_DIR / "digest_anchor.json"
INDEX_PATH = CHATS_DIR / "_index.json"

# ── Settings ─────────────────────────────────────
def load_settings():
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    keys = {"gemini_key": "", "siliconflow_key": "", "gemini_free_key": "", "aipro_key": ""}
    txt = BASE_DIR.parent / "所需要的API.txt"
    if txt.exists():
        with open(txt, "r", encoding="utf-8") as f:
            for line in f:
                if "gemini-api" in line.lower():
                    keys["gemini_key"] = line.split("：")[-1].strip()
                elif "硅基流动" in line.lower() and "api" in line.lower():
                    keys["siliconflow_key"] = line.split("：")[-1].strip()
    save_settings(keys)
    return keys

def save_settings(data: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

SETTINGS = load_settings()

SENTINEL_ROUTES = {"original", "codex_luna"}
DEFAULT_SENTINEL_CODEX_MODEL = "gpt-5.6-luna"
_CODEX_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def normalize_sentinel_codex_model(value) -> str:
    """哨兵走 Codex 登录态时用哪个模型：小猫在设置里自己选，模型下架了能换新的。不合法就用默认。"""
    model = str(value or "").strip()
    return model if _CODEX_MODEL_ID.match(model) else DEFAULT_SENTINEL_CODEX_MODEL


def normalize_sentinel_route(value) -> str:
    route = str(value or "original").strip().lower()
    return route if route in SENTINEL_ROUTES else "original"

def get_key(provider: str) -> str:
    if provider == "elevenlabs":
        return SETTINGS.get("elevenlabs_tts_key", "")
    if provider == "gemini":
        return SETTINGS.get("gemini_key", "")
    if provider == "gemini_free":
        return SETTINGS.get("gemini_free_key", "") or SETTINGS.get("gemini_key", "")
    if provider == "aipro":
        return SETTINGS.get("aipro_key", "")
    if provider == "minimax":
        return SETTINGS.get("minimax_tts_key", "")
    return SETTINGS.get("siliconflow_key", "")

def get_sentinel_config() -> dict:
    """
    返回哨兵模型配置。Luna 使用本机 Codex 登录态；原线路保持兼容。
    """
    route = normalize_sentinel_route(SETTINGS.get("sentinel_route"))
    if route == "codex_luna":
        return {
            "route": "codex_luna",
            "provider": "codex",
            "base_url": "",
            "api_key": "",
            "model": normalize_sentinel_codex_model(SETTINGS.get("sentinel_codex_model")),
            "reasoning_effort": "none",
            "use_openai": False,
            "ready": True,
        }

    base_url = SETTINGS.get("sentinel_base_url", "").strip()
    api_key = SETTINGS.get("sentinel_api_key", "").strip()
    model = SETTINGS.get("sentinel_model", "").strip()
    if base_url and api_key:
        # 自定义中转站 / 硅基流动等 OpenAI 兼容
        return {
            "route": "original",
            "provider": "openai_compatible",
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "model": model or "Qwen/Qwen3.6-35B-A3B",
            "use_openai": True,
            "ready": True,
        }
    # 默认走 Gemini 原生
    gemini_key = get_key("gemini_free")
    return {
        "route": "original",
        "provider": "gemini",
        "base_url": "",
        "api_key": gemini_key,
        "model": model or "gemini-3.1-flash-lite",
        "use_openai": False,
        "ready": bool(gemini_key),
    }

def get_embedding_config() -> dict:
    """
    返回向量模型配置。
    若用户配置了自定义 URL，走 OpenAI 兼容格式；否则走 Gemini 原生 API。
    返回: {"base_url": str, "api_key": str, "model": str, "use_openai": bool}
    """
    base_url = SETTINGS.get("embedding_base_url", "").strip()
    api_key = SETTINGS.get("embedding_api_key", "").strip()
    model = SETTINGS.get("embedding_model", "").strip()
    if base_url and api_key:
        return {
            "base_url": base_url.rstrip("/"),
            "api_key": api_key,
            "model": model or "Qwen/Qwen3-Embedding-8B",
            "use_openai": True,
        }
    # 默认走 Gemini 原生
    return {
        "base_url": "",
        "api_key": get_key("gemini_free"),
        "model": model or "gemini-embedding-001",
        "use_openai": False,
    }

# ── Worldbook ────────────────────────────────────
def _default_worldbook() -> dict:
    return {
        "ai_persona": "",
        "user_persona": "",
        "system_prompt": "",
        "system_prompt_enabled": True,
        "ai_name": "AI",
        "user_name": "你",
        "persona_schema_version": 1,
        "ai_persona_sections": {},
        "user_persona_sections": {},
        "creative_rules": "",
        "persona_section_locks": {},
        "persona_evolution_enabled": False,
    }

def load_worldbook():
    defaults = _default_worldbook()
    if WORLDBOOK_PATH.exists():
        try:
            data = json.loads(WORLDBOOK_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                defaults.update(data)
                return defaults
        except:
            pass
    return defaults

def save_worldbook(data: dict):
    WORLDBOOK_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# ── Chat Status ──────────────────────────────────
def load_chat_status() -> dict:
    if CHAT_STATUS_PATH.exists():
        try:
            return json.loads(CHAT_STATUS_PATH.read_text(encoding='utf-8'))
        except:
            pass
    return {"status": "", "updated_at": 0}

def save_chat_status(status: str):
    data = {"status": status, "updated_at": time.time()}
    CHAT_STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# ── Digest Anchor ────────────────────────────────
def load_digest_anchor() -> float:
    """返回上次总结的时间戳锚点，0.0 表示从未总结过"""
    if DIGEST_ANCHOR_PATH.exists():
        try:
            data = json.loads(DIGEST_ANCHOR_PATH.read_text(encoding='utf-8'))
            return float(data.get("last_digest_ts", 0.0))
        except:
            pass
    return 0.0

def save_digest_anchor(ts: float):
    data = {"last_digest_ts": ts, "updated_at": time.time()}
    DIGEST_ANCHOR_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# ── 文件索引 ─────────────────────────────────────
def load_file_index():
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding='utf-8'))
        except:
            return {}
    return {}

def save_file_index(idx):
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding='utf-8')

def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|\n\r]', '_', name).strip().rstrip('.')

# ── 模型配置 ─────────────────────────────────────
CUSTOM_OPENAI_PROVIDER = "custom_openai"
CUSTOM_REASONING_EFFORTS = {"low", "high", "max"}
MODEL_TRANSPORT_MODES = {"legacy", "safe_live"}
SAFE_LIVE_MODEL_DEFAULTS = {
    "Codex-Astra": "safe_live",
    "Codex-Sol": "safe_live",
    "Codex-6-Sol": "safe_live",
    "Codex-6.1-Sol": "safe_live",
    "3.8Vertex": "safe_live",
    "官Gem3.8flash": "safe_live",
}
DEPRECATED_MODEL_PROVIDERS = {"gemini_cli"}
DEPRECATED_MODEL_KEYS = {"CLI-3.1pro", "AGY-3.1pro"}
ANTIGRAVITY_MODELS_PATH = DATA_DIR / "antigravity_models.json"

# AGY 模型开关：只展示未注释的官方 slug。取消注释后重启服务器、刷新页面。
# antigravity_models.json 是自动生成的缓存，不要在那里修改开关。
ANTIGRAVITY_ENABLED_MODELS = (
    "gemini-3.8-flash-high",
    # "gemini-3.8-flash-medium",
    # "gemini-3.8-flash-low",
    # "gemini-3.7-flash-high",
    # "gemini-3.7-flash-medium",
    # "gemini-3.7-flash-low",
    # "gemini-3.6-flash-high",
    # "gemini-3.6-flash-medium",
    # "gemini-3.6-flash-low",
    # "gemini-3.1-pro-high",
    # "gemini-3.1-pro-low",
    # "claude-opus-5-5-low",
    "claude-opus-5-5-medium",
    # "claude-opus-5-5-high",
    # "claude-sonnet-5-5-low",
    # "claude-sonnet-5-5-medium",
    # "claude-sonnet-5-5-high",
    # "gpt-oss-120b-medium",
)


def _read_antigravity_models() -> dict:
    try:
        rows = json.loads(ANTIGRAVITY_MODELS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, dict):
        return {}
    return {
        key: value for key, value in rows.items()
        if isinstance(key, str) and key.startswith("AGY · ")
        and isinstance(value, dict) and value.get("provider") == "antigravity_cli"
        and value.get("model") in ANTIGRAVITY_ENABLED_MODELS
    }


ANTIGRAVITY_MODELS = _read_antigravity_models()

BUILTIN_MODELS = {
    "硅基GLM-5.2":      {"provider": "siliconflow", "model": "zai-org/GLM-5.2", "vision": False},
    "硅基DSv4pro":      {"provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V4-Pro", "vision": False},
    "官Gem3.8flash":  {"provider": "gemini", "model": "gemini-3.8-flash", "vision": True},
    # "官Gem3.1pro":  {"provider": "gemini", "model": "gemini-3.1-pro-preview", "vision": True},
    # "Codex-5.5":            {"provider": "codex_cli",  "model": "gpt-5.5", "vision": True},
    # "Codex-Astra":    {"provider": "codex_cli",  "model": "gpt-6-astra", "vision": True, "transport_mode": "safe_live"},
    "Codex-5.6-Sol":      {"provider": "codex_cli",  "model": "gpt-5.6-sol", "vision": True, "transport_mode": "safe_live"},
    "Codex-6-Sol":    {"provider": "codex_cli",  "model": "gpt-6-sol", "vision": True, "transport_mode": "safe_live"},
    "Codex-6.1-Sol":  {"provider": "codex_cli",  "model": "gpt-6.1-sol", "vision": True, "transport_mode": "safe_live"},
    # "Codex-Luna":     {"provider": "codex_cli",  "model": "gpt-5.6-luna", "vision": True},
    # "AGY（默认）": {"provider": "antigravity_cli", "model": "", "vision": False, "audio": False},
    
}


def _clean_text(value) -> str:
    return str(value or "").strip()


def normalize_model_transport_modes(value) -> dict[str, str]:
    """Keep only explicit, supported per-model transport overrides."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_mode in value.items():
        key = _clean_text(raw_key)
        mode = _clean_text(raw_mode).lower()
        if key and mode in MODEL_TRANSPORT_MODES:
            normalized[key] = mode
    return normalized


def normalize_custom_model_routes(value) -> list[dict]:
    """把设置页保存的自定义 OpenAI 兼容线路清洗成稳定结构。"""
    if not isinstance(value, list):
        return []
    routes: list[dict] = []
    seen_route_ids: set[str] = set()
    seen_model_keys: set[str] = set()
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        base_url = _clean_text(item.get("base_url")).rstrip("/")
        api_key = _clean_text(item.get("api_key"))
        raw_models = item.get("models")
        if not base_url or not isinstance(raw_models, list):
            continue
        route_id = _clean_text(item.get("id")) or f"custom_route_{idx}"
        route_id = sanitize_filename(route_id).replace(" ", "_") or f"custom_route_{idx}"
        while route_id in seen_route_ids:
            route_id = f"{route_id}_{idx}"
        seen_route_ids.add(route_id)
        route_name = _clean_text(item.get("name")) or f"自定义线路 {idx}"
        models: list[dict] = []
        for raw_model in raw_models:
            if isinstance(raw_model, str):
                model_id = _clean_text(raw_model)
                model_key = model_id
                vision = True
                audio = False
                video = False
                use_default_reasoning_effort = True
                reasoning_effort = "high"
            elif isinstance(raw_model, dict):
                model_id = _clean_text(raw_model.get("model") or raw_model.get("model_id"))
                model_key = _clean_text(raw_model.get("key") or raw_model.get("name") or model_id)
                vision = bool(raw_model.get("vision", True))
                audio = raw_model.get("audio") is True
                video = raw_model.get("video") is True
                use_default_reasoning_effort = raw_model.get("use_default_reasoning_effort") is not False
                reasoning_effort = _clean_text(raw_model.get("reasoning_effort")).lower()
                if reasoning_effort == "medium":
                    reasoning_effort = "high"
                if reasoning_effort not in CUSTOM_REASONING_EFFORTS:
                    reasoning_effort = "high"
            else:
                continue
            if not model_id or not model_key:
                continue
            if model_key in BUILTIN_MODELS or model_key in seen_model_keys:
                continue
            seen_model_keys.add(model_key)
            models.append({
                "key": model_key,
                "model": model_id,
                "vision": vision,
                "audio": audio,
                "video": video,
                "use_default_reasoning_effort": use_default_reasoning_effort,
                "reasoning_effort": reasoning_effort,
            })
        if models:
            routes.append({
                "id": route_id,
                "name": route_name,
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
                # 缓存标记（默认开）：在不变的人设 / 规则末尾标一下，Claude 能缓存这一段；中转站不认会自动去掉
                "cache_marker": item.get("cache_marker") is not False,
                # 缓存保留多久：1h（默认，断断续续聊也能命中；写入价约为读入的 2 倍）或 5m（写入约 1.25 倍）
                "cache_ttl": "5m" if item.get("cache_ttl") == "5m" else "1h",
            })
    return routes


# 每位 AI 在名片里自己填的线路（含 Key）单独存在这里：设置页的接口不返回、也不会整份覆盖它
SEAT_ROUTES_KEY = "seat_model_routes"
SEAT_ROUTE_PREFIX = "seat_"


def _migrate_seat_routes() -> bool:
    """旧版本把座位线路混在 custom_model_routes 里，挪到 seat_model_routes。返回是否有改动。"""
    custom = SETTINGS.get("custom_model_routes")
    if not isinstance(custom, list):
        return False
    moving = [r for r in custom if isinstance(r, dict) and str(r.get("id") or "").startswith(SEAT_ROUTE_PREFIX)]
    if not moving:
        return False
    seats = [r for r in (SETTINGS.get(SEAT_ROUTES_KEY) or []) if isinstance(r, dict)]
    have = {r.get("id") for r in seats}
    SETTINGS[SEAT_ROUTES_KEY] = seats + [r for r in moving if r.get("id") not in have]
    SETTINGS["custom_model_routes"] = [r for r in custom if r not in moving]
    return True


def route_model_keys(routes) -> set[str]:
    return {m["key"] for r in normalize_custom_model_routes(routes) for m in r.get("models", [])}


def refresh_custom_models() -> None:
    """根据 settings.json 里的自定义线路（每位 AI 自己填的 + 设置页的）刷新运行时模型列表。

    座位线路排在前面：万一两边重名，座位的绑定不会被设置页的线路顶掉（保存时两边都会拒绝重名）。
    """
    if _migrate_seat_routes():
        save_settings(SETTINGS)
    MODELS.clear()
    MODELS.update(BUILTIN_MODELS)
    MODELS.update(ANTIGRAVITY_MODELS)
    routes = normalize_custom_model_routes(SETTINGS.get(SEAT_ROUTES_KEY))
    routes += normalize_custom_model_routes(SETTINGS.get("custom_model_routes"))
    for route in routes:
        for item in route.get("models", []):
            key = item["key"]
            if key in MODELS:  # 两边重名时先到先得，不互相覆盖
                continue
            MODELS[key] = {
                "provider": CUSTOM_OPENAI_PROVIDER,
                "model": item["model"],
                "vision": bool(item.get("vision", True)),
                "audio": item.get("audio") is True,
                "video": item.get("video") is True,
                "use_default_reasoning_effort": item.get("use_default_reasoning_effort") is not False,
                "reasoning_effort": item.get("reasoning_effort", "high"),
                "base_url": route["base_url"],
                "api_key": route.get("api_key", ""),
                "route_id": route["id"],
                "route_name": route["name"],
                "cache_marker": route.get("cache_marker", True) is not False,
                "cache_ttl": route.get("cache_ttl", "1h"),
            }
    # Codex 账号里的模型也是运行时补进来的：重建列表时一起补回去（启动时由 main 补一次）
    if "ai_providers" in sys.modules:
        try:
            import codex_models
            codex_models.sync_models()
        except Exception as error:
            print(f"[codex models] 同步失败: {type(error).__name__}")


def replace_antigravity_models(models: dict, *, persist: bool = False) -> None:
    """Apply the AGY switches to the official catalog and its saved cache."""
    models = {key: value for key, value in models.items()
              if value.get("model") in ANTIGRAVITY_ENABLED_MODELS}
    for key in ANTIGRAVITY_MODELS:
        MODELS.pop(key, None)
    ANTIGRAVITY_MODELS.clear()
    ANTIGRAVITY_MODELS.update(models)
    MODELS.update(ANTIGRAVITY_MODELS)
    if persist:
        temporary = ANTIGRAVITY_MODELS_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(ANTIGRAVITY_MODELS_PATH)


MODELS = {}
refresh_custom_models()

DEFAULT_MODEL = "Gemini-3.5-flash"


def model_supports_safe_live(model_key: str | None) -> bool:
    """Only individually approved model routes may use safe-live."""
    key = _clean_text(model_key)
    return key in MODELS and key in SAFE_LIVE_MODEL_DEFAULTS


def resolve_model_transport_mode(model_key: str | None) -> str:
    """Resolve one request's transport mode, always failing closed to legacy."""
    key = _clean_text(model_key)
    if not model_supports_safe_live(key):
        return "legacy"
    overrides = normalize_model_transport_modes(SETTINGS.get("model_transport_modes"))
    if key in overrides:
        return overrides[key]
    configured = _clean_text((MODELS.get(key) or {}).get("transport_mode")).lower()
    if configured in MODEL_TRANSPORT_MODES:
        return configured
    return SAFE_LIVE_MODEL_DEFAULTS.get(key, "legacy")


def is_model_deprecated(model_key: str | dict | None) -> bool:
    if isinstance(model_key, dict):
        return (model_key.get("provider") or "") in DEPRECATED_MODEL_PROVIDERS
    key = _clean_text(model_key)
    if key in DEPRECATED_MODEL_KEYS:
        return True
    return (MODELS.get(key) or {}).get("provider", "") in DEPRECATED_MODEL_PROVIDERS


def model_usable(cfg: dict) -> bool:
    """这条线路在这台服务器上用不用得了：中转站总能用；内置线路要有对应的 Key，
    命令行线路（Codex / Gemini CLI / Antigravity）要本机装了那个程序。用不了的就不出现在模型列表里。"""
    provider = (cfg or {}).get("provider", "")
    if provider == "custom_openai":
        return True
    if provider in ("siliconflow", "gemini", "aipro"):
        return bool(str(get_key(provider) or "").strip())
    try:
        if provider == "codex_cli":
            import ai_providers
            if not ai_providers._CODEX_SCRIPT:
                return False
            import codex_models
            available = codex_models.available_slugs()
            model = str(cfg.get("model") or "")
            # 没写模型名的用 Codex 自己的默认模型；账号目录未知时照旧可用
            return available is None or not model or model in available
        if provider == "gemini_cli":
            import ai_providers
            return bool(ai_providers._GEMINI_SCRIPT)
        if provider == "antigravity_cli":
            import shutil
            return bool(shutil.which("agy") or shutil.which("agy.exe"))
    except Exception:
        return False
    return True


def iter_visible_models():
    for key, cfg in MODELS.items():
        if not is_model_deprecated(key):
            yield key, cfg


def iter_selectable_models():
    """给页面下拉框用：在可见的基础上，再去掉这台服务器上用不了的内置线路。"""
    for key, cfg in iter_visible_models():
        if model_usable(cfg):
            yield key, cfg


class ModelChanged(RuntimeError):
    """「必须用这位 AI 自己的模型」的生成途中，那条线路被删、停用或改了。"""


def model_snapshot(model_key: str) -> dict | None:
    """固定一条模型线路此刻的样子（地址、模型名、线路 id 等）；不存在或已停用返回 None。"""
    key = _clean_text(model_key)
    if key not in MODELS or is_model_deprecated(key):
        return None
    return dict(MODELS[key])


def ensure_model_unchanged(model_key: str, snapshot: dict | None) -> None:
    """生成前核对：线路还在、没变、而且不会被 resolve_model_key 悄悄换成别的模型。否则抛 ModelChanged。

    调用方要在这里和真正发起调用之间不 await，核对和调用才算同一时刻。
    """
    key = _clean_text(model_key)
    if snapshot is None or MODELS.get(key) != snapshot or resolve_model_key(key) != key:
        raise ModelChanged(f"模型线路「{key}」刚刚被删除或改动了，这次不生成（不会换成别的模型）")


_DATE_SUFFIX = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")

# 能力块里「每轮都可能变」的部分（日程、位置、设备、画像）从这个小标题开始；缓存标记只盖到它之前
LIVE_STATE_HEADING = "【实时状态（每轮可能变化）】"


def model_id_core(model_id: str) -> str:
    """模型 ID 去掉中转站常加的厂商前缀（anthropic/、models/），统一小写。"""
    return str(model_id or "").strip().lower().rsplit("/", 1)[-1]


def same_model_id(x: str, y: str) -> bool:
    """两个模型 ID 是不是同一个模型。只容许一边多一个完整日期后缀（-20250805 或 -2025-08-05），
    版本号不同（claude-sonnet-4 和 claude-sonnet-4-5）算不同模型。"""
    x, y = model_id_core(x), model_id_core(y)
    if not x or not y:
        return False
    return x == y or _DATE_SUFFIX.sub("", x) == y or _DATE_SUFFIX.sub("", y) == x


def same_underlying_model(a: str, b: str) -> bool:
    """两条线路（模型键）是不是同一个模型，只是走不同的中转站。"""
    return same_model_id((MODELS.get(a) or {}).get("model", ""), (MODELS.get(b) or {}).get("model", ""))


def default_model_key() -> str:
    """默认模型：小猫在设置里选的（还能用的话）；没选或已不能用时，取第一个这台服务器上能用的。"""
    chosen = _clean_text(SETTINGS.get("default_model"))
    if chosen in MODELS and is_shared_model(chosen) and not is_model_deprecated(chosen) and model_usable(MODELS[chosen]):
        return chosen
    for key, _cfg in iter_selectable_models():
        if is_shared_model(key):
            return key
    return ""  # 一条能用的共用线路都没有：调用时会提示去设置里接线路，不拿别的模型顶替


NO_ROUTE_HINT = "还没有能用的线路：请到 设置 → 🧭 模型线路 接一条中转站（Codex 只给 Lucien 用）"


def is_shared_model(model_key: str) -> bool:
    """大家都能用的线路。不算：某位 AI 自己填的专属线路（座位线路）；Codex 登录态（是 GPT，只给 Lucien 用，
    不能当默认落到小克等别人身上，否则就是 GPT 顶着别人的人设说话）。"""
    cfg = MODELS.get(model_key) or {}
    if cfg.get("provider") == "codex_cli":
        return False
    return not str(cfg.get("route_id") or "").startswith(SEAT_ROUTE_PREFIX)


CODEX_ONLY_FOR_LUCIEN = "Codex 是 GPT，只给 Lucien 用"


def others_private_route(actor: str, model_key: str | None) -> bool:
    """别人的专属线路（座位自己填的 API：route_id = seat_<那位>）不给这位 AI 用，免得穿错衣服。"""
    route_id = str((MODELS.get(_clean_text(model_key)) or {}).get("route_id") or "")
    return route_id.startswith(SEAT_ROUTE_PREFIX) and route_id != f"{SEAT_ROUTE_PREFIX}{actor}"


def codex_forbidden(actor: str, model_key: str | None) -> bool:
    """Codex 登录态（GPT）只给 Lucien（connor）；小克和座位 3～6 不能用它（会变成 GPT 顶着别人的人设）。"""
    return actor != "connor" and (MODELS.get(_clean_text(model_key)) or {}).get("provider") == "codex_cli"


async def scrub_codex_choices() -> list[str]:
    """启动时清一次以前存下的「小克 / 座位用 Codex」：小克私聊窗口存的模型换成默认模型（可能为空），
    聊天室的小克模型、名片里小克和座位的模型 / 后台线路清空。Lucien 的不动。返回改了哪些（只记位置不记内容）。"""
    changed: list[str] = []
    codex_keys = [key for key, cfg in MODELS.items() if cfg.get("provider") == "codex_cli"]
    if codex_keys:
        from database import get_db
        replacement = default_model_key()
        marks = ",".join("?" for _ in codex_keys)
        async with get_db() as db:
            cur = await db.execute(f"UPDATE conversations SET model=? WHERE model IN ({marks})",
                                   (replacement, *codex_keys))
            if cur.rowcount:
                changed.append(f"conversations×{cur.rowcount}")
            await db.commit()
    from chatroom import load_chatroom_config, save_chatroom_config
    room_cfg = load_chatroom_config()
    if codex_forbidden("aion", room_cfg.get("aion_model")):
        room_cfg["aion_model"] = ""
        save_chatroom_config(room_cfg)
        changed.append("chatroom.aion_model")
    import actors
    raw = actors._load_raw()
    seats = raw.get("seats") if isinstance(raw.get("seats"), dict) else {}
    for actor_id, stored in seats.items():
        if not isinstance(stored, dict):
            continue
        for field in ("model", "background_model"):
            if codex_forbidden(actor_id, stored.get(field)):
                stored[field] = ""
                changed.append(f"{actor_id}.{field}")
    if any("." in item and not item.startswith("chatroom") for item in changed):
        actors._save_raw(raw)
    return changed


def aion_own_model() -> str:
    """小克自己的模型：相遇卡（名片）里给他选的，其次是群聊设置里给他选的；不能用、是 Codex 或已停用的不算。"""
    candidates = []
    try:
        import actors
        candidates.append((actors.get_actor("aion") or {}).get("model"))
    except Exception:
        pass
    try:
        from chatroom import load_chatroom_config
        candidates.append(load_chatroom_config().get("aion_model"))
    except Exception:
        pass
    for candidate in candidates:
        key = _clean_text(candidate)
        if (key in MODELS and not is_model_deprecated(key) and not codex_forbidden("aion", key)
                and not others_private_route("aion", key) and model_usable(MODELS[key])):
            return key
    return ""


def aion_model_key(model_key: str | None) -> str:
    """小克用的模型（私聊、群聊、自主活动）。选了、还在、不是 Codex、不是别人专属线路的就用它；
    没选 / 已不存在 / 是 Codex（只给 Lucien）时，先用小克自己的模型（名片里选的），再退到默认模型；
    都没有时返回空，调用时提示去接线路。"""
    key = _clean_text(model_key)
    if (key in MODELS and not is_model_deprecated(key) and not codex_forbidden("aion", key)
            and not others_private_route("aion", key)):
        return key
    return aion_own_model() or default_model_key()


def resolve_model_key(model_key: str | None) -> str:
    """选过的模型还在就用它；空的、已不存在或已停用的（比如原作者写死的旧默认值）换成默认模型。
    还在、只是暂时用不了的不偷偷换（座位等「必须用自己的模型」的规矩靠这个）。"""
    key = _clean_text(model_key)
    if key in MODELS and not is_model_deprecated(key):
        return key
    return default_model_key()

# ── 摄像头默认配置 ───────────────────────────────
DEFAULT_CAM_CFG = {
    "camera_index": 0,
    "active_source": "local",
    "esp32_cam_url": "",
    "auto_interval_min": 10,
    "auto_interval_max": 20,
    "max_screenshots": 200,
    "include_pc_screen": False,
    "monitor_enabled": False,
    "quiet_hours_enabled": False,
    "quiet_hours_start": "00:00",
    "quiet_hours_end": "09:00",
    "wake_mode": "aion",
    # 出门 / 回家时叫谁：smart = 哨兵按情况挑（拿不准就随机），也可以固定 aion / connor / 座位
    "location_wake_mode": "smart",
}

CAM_WAKE_MODES = {"aion", "connor", "smart", "ai3", "ai4", "ai5", "ai6"}  # 座位 3～6 也可以固定叫


def normalize_camera_wake_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in CAM_WAKE_MODES else "aion"

def load_cam_config() -> dict:
    if CAM_CONFIG_PATH.exists():
        with open(CAM_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 兼容旧配置：将 auto_interval（秒）迁移为 min/max（分钟）
        if "auto_interval" in cfg and "auto_interval_min" not in cfg:
            old_minutes = max(1, cfg.pop("auto_interval", 600) // 60)
            cfg["auto_interval_min"] = old_minutes
            cfg["auto_interval_max"] = old_minutes
        elif "auto_interval" in cfg:
            cfg.pop("auto_interval", None)
        for k, v in DEFAULT_CAM_CFG.items():
            cfg.setdefault(k, v)
        cfg["wake_mode"] = normalize_camera_wake_mode(cfg.get("wake_mode"))
        cfg["location_wake_mode"] = normalize_camera_wake_mode(cfg.get("location_wake_mode") or "smart")
        return cfg
    return dict(DEFAULT_CAM_CFG)

def save_cam_config(cfg: dict):
    with open(CAM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ── 允许上传的文件类型 ────────────────────────────
ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp',
                 'video/mp4', 'video/webm', 'video/quicktime',
                 'audio/webm', 'audio/ogg', 'audio/wav', 'audio/mp4',
                 'audio/mpeg', 'audio/x-wav'}
