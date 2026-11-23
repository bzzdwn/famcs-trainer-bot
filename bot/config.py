"""Настройки из .env (сам .env в git не попадает)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_tg_id: int
    group_code: str
    db_path: str
    templates_dir: Path
    yandex_api_key: str | None = None      # без ключей фото-ввод выключен
    yandex_folder_id: str | None = None
    formula_url: str = "https://bzzdwn.github.io/famcs-trainer-bot/formula/"   # страница docs/formula (GitHub Pages)


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    missing = [k for k in ("BOT_TOKEN", "ADMIN_TG_ID", "GROUP_CODE") if not os.getenv(k)]
    if missing:
        raise SystemExit(f"В .env не заданы: {', '.join(missing)} (образец — .env.example)")
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        admin_tg_id=int(os.environ["ADMIN_TG_ID"]),
        group_code=os.environ["GROUP_CODE"],
        db_path=os.getenv("DB_PATH", str(ROOT / "data" / "bot.db")),
        templates_dir=ROOT / "data" / "templates",
        yandex_api_key=os.getenv("YANDEX_API_KEY") or None,
        yandex_folder_id=os.getenv("YANDEX_FOLDER_ID") or None,
        formula_url=os.getenv("FORMULA_URL") or Settings.formula_url,
    )
