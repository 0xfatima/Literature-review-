import pathlib

from app.core import config


def test_repo_root_env_file_is_resolved_independently_of_cwd():
    """alembic/uvicorn/celery are started from services/api in the README; the root .env must still be found."""
    root_env = config._REPO_ROOT_ENV
    assert root_env.name == ".env" and root_env.is_absolute()
    assert (root_env.parent / "pyproject.toml").is_file() and (root_env.parent / "docker-compose.yml").is_file()
    assert config.Settings.model_config["env_file"][0] == root_env


def test_environment_variables_override_env_files(monkeypatch, tmp_path: pathlib.Path):
    env = tmp_path / ".env"
    env.write_text("LOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert config.Settings(_env_file=env).log_level == "WARNING"  # type: ignore[call-arg]
    monkeypatch.delenv("LOG_LEVEL")
    assert config.Settings(_env_file=env).log_level == "DEBUG"  # type: ignore[call-arg]
