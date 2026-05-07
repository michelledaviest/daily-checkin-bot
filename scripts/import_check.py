"""Import-only smoke test. Stubs every external dependency so we can verify our
own module wiring without installing the world. Run from the repo root:

    python3 scripts/import_check.py
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---- minimal env so config.py doesn't blow up
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_ALLOWED_CHAT_ID", "0")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("GSA_KEY_PATH", "/tmp/none.json")
os.environ.setdefault("SHEET_ID", "test")


def stub(name: str, **attrs) -> types.ModuleType:
    """Register a stub module under sys.modules and populate attrs."""
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        partial = ".".join(parts[:i])
        if partial not in sys.modules:
            sys.modules[partial] = types.ModuleType(partial)
    mod = sys.modules[name]
    for k, v in attrs.items():
        setattr(mod, k, v)
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        setattr(sys.modules[parent_name], child, mod)
    return mod


# ---- python-dotenv
stub("dotenv", load_dotenv=lambda *a, **k: None)


# ---- google.genai
class _StubClient:
    def __init__(self, *a, **k): self.models = self
    def generate_content(self, *a, **k): return types.SimpleNamespace(parsed=None, text="{}")


_genai = stub(
    "google.genai",
    Client=_StubClient,
)
_types = stub("google.genai.types")


class _Part:
    @classmethod
    def from_bytes(cls, data, mime_type): return object()
    @classmethod
    def from_text(cls, text): return object()


class _Content:
    def __init__(self, role, parts): self.role, self.parts = role, parts


class _Cfg:
    def __init__(self, **k): self.k = k


_types.Part = _Part
_types.Content = _Content
_types.GenerateContentConfig = _Cfg

stub("google")
sys.modules["google"].genai = _genai


# ---- gspread + google.oauth2.service_account
class _Worksheet:
    def append_row(self, *a, **k): pass


class _GSpreadClient:
    def open_by_key(self, *a, **k): return self
    def worksheet(self, *a, **k): return _Worksheet()


_gspread = stub(
    "gspread",
    authorize=lambda creds: _GSpreadClient(),
    Worksheet=_Worksheet,
)


class _Creds:
    @classmethod
    def from_service_account_file(cls, path, scopes): return cls()


stub("google.oauth2")
stub("google.oauth2.service_account", Credentials=_Creds)


# ---- requests
stub("requests", get=lambda *a, **k: None, RequestException=Exception)


# ---- apscheduler
class _Scheduler:
    def __init__(self, **k): pass
    def add_job(self, *a, **k): pass
    def start(self): pass
    def shutdown(self, **k): pass


stub("apscheduler.schedulers.asyncio", AsyncIOScheduler=_Scheduler)
stub("apscheduler.triggers.cron", CronTrigger=lambda **k: None)
stub("apscheduler.triggers.interval", IntervalTrigger=lambda **k: None)


# ---- telegram + telegram.ext
class _Bot:
    async def send_message(self, *a, **k): pass


class _Update: pass


class _CommandHandler:
    def __init__(self, *a, **k): pass


class _MessageHandler:
    def __init__(self, *a, **k): pass


class _CallbackQueryHandler:
    def __init__(self, *a, **k): pass


class _Filters:
    VOICE = object()
    AUDIO = object()
    TEXT = object()
    COMMAND = object()


_filters_mod = types.ModuleType("telegram.ext.filters")
_filters_mod.VOICE = _Filters.VOICE
_filters_mod.AUDIO = _Filters.AUDIO
_filters_mod.TEXT = _Filters.TEXT
_filters_mod.COMMAND = _Filters.COMMAND


class _AppBuilder:
    def token(self, *a, **k): return self
    def post_init(self, *a, **k): return self
    def post_shutdown(self, *a, **k): return self
    def build(self):
        a = types.SimpleNamespace()
        a.add_handler = lambda h: None
        a.bot_data = {}
        a.bot = _Bot()
        a.run_polling = lambda **k: None
        return a


class _Application:
    @staticmethod
    def builder(): return _AppBuilder()


class _ContextTypes:
    DEFAULT_TYPE = type("CT", (), {})


class _InlineKeyboardButton:
    def __init__(self, *a, **k): pass


class _InlineKeyboardMarkup:
    def __init__(self, *a, **k): pass


stub(
    "telegram",
    Update=_Update,
    Bot=_Bot,
    InlineKeyboardButton=_InlineKeyboardButton,
    InlineKeyboardMarkup=_InlineKeyboardMarkup,
)
stub(
    "telegram.ext",
    Application=_Application,
    CallbackQueryHandler=_CallbackQueryHandler,
    CommandHandler=_CommandHandler,
    ContextTypes=_ContextTypes,
    MessageHandler=_MessageHandler,
    filters=_filters_mod,
)
sys.modules["telegram.ext.filters"] = _filters_mod


# ---- pydantic — try real, fall back to a tiny stub
try:
    import pydantic  # noqa: F401
except ImportError:
    pyd = stub("pydantic")

    class _BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items(): setattr(self, k, v)
        @classmethod
        def model_validate(cls, d):
            return cls(**(d or {}))
        @classmethod
        def model_validate_json(cls, s):
            import json
            return cls.model_validate(json.loads(s))
        def model_dump(self): return self.__dict__

    pyd.BaseModel = _BaseModel
    pyd.Field = lambda *a, **k: None


# Now import every project module.
from src import (  # noqa: F401
    analytics,
    archive,
    config,
    date_parsing,
    gemini,
    hydration,
    monitoring,
    prompts,
    scheduler,
    sheets,
    state,
    telegram_handlers,
    time_util,
    weather,
)
import src.main  # noqa: F401

print("OK: all modules import cleanly with stubs.")
print(f"Required morning fields: {len(prompts.REQUIRED_FIELDS_MORNING)}")
print(f"Required evening fields: {len(prompts.REQUIRED_FIELDS_EVENING)}")
print(f"Sheet column count: {len(sheets.COLUMN_ORDER)}")
print(f"Slot opener (morning): {prompts.slot_opener('morning')[:60]}...")
