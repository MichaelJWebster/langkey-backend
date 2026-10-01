"""Production-safety guard: dev-grade secrets must not boot outside dev."""

import pytest

from app.config import DEV_JWT_SECRET, Settings, check_production_config


def test_dev_environment_allows_dev_secret() -> None:
    check_production_config(Settings(environment="dev", jwt_secret=DEV_JWT_SECRET))


def test_prod_rejects_dev_secret() -> None:
    with pytest.raises(RuntimeError, match="LANGKEY_JWT_SECRET"):
        check_production_config(Settings(environment="prod", jwt_secret=DEV_JWT_SECRET))


def test_prod_accepts_real_secret() -> None:
    check_production_config(Settings(environment="prod", jwt_secret="x" * 48))
