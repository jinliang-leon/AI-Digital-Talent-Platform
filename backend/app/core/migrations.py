from alembic.config import Config

from .config import ROOT


def config_for():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic").replace("%", "%%"))
    return config
