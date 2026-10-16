from __future__ import annotations

from pathlib import Path

import pytest

from scripts import validate_experimental_candidate_config as candidate_config
from scripts.validate_experimental_candidate_config import validate

REVISION = "a" * 40


def _write_candidate_files(root: Path) -> Path:
    root.mkdir(mode=0o700)
    password = root / "postgres-password"
    password.write_text("private-password\n")
    password.chmod(0o600)
    env_file = root / "candidate.env"
    env_file.write_text(
        "\n".join(
            (
                f"CANDIDATE_IMAGE_TAG={REVISION}",
                "CANDIDATE_API_KEY=private-api-key",
                "LLM_API_KEY=private-llm-key",
                f"CANDIDATE_POSTGRES_PASSWORD_FILE={password}",
            )
        )
        + "\n"
    )
    env_file.chmod(0o600)
    return env_file


def _treat_tmp_path_as_persistent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(candidate_config, "VOLATILE_ROOTS", (Path("/never-used"),))


def test_accepts_private_persistent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _treat_tmp_path_as_persistent(monkeypatch)
    validate(_write_candidate_files(tmp_path / "candidate"))


@pytest.mark.parametrize("quote", ["'", '"'])
def test_accepts_compose_env_quoted_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quote: str
) -> None:
    _treat_tmp_path_as_persistent(monkeypatch)
    env_file = _write_candidate_files(tmp_path / "candidate")
    text = env_file.read_text()
    for value in (REVISION, "private-api-key", "private-llm-key"):
        text = text.replace(f"={value}", f"={quote}{value}{quote}")
    env_file.write_text(text)
    validate(env_file)


@pytest.mark.parametrize("root", [Path("/tmp"), Path("/private/tmp"), Path("/var/tmp")])
def test_rejects_volatile_environment_file(root: Path) -> None:
    with pytest.raises(ValueError, match="volatile temporary storage"):
        validate(root / "candidate.env")


def test_rejects_volatile_password_file(tmp_path: Path) -> None:
    env_file = _write_candidate_files(tmp_path / "candidate")
    text = env_file.read_text().replace(
        f"CANDIDATE_POSTGRES_PASSWORD_FILE={tmp_path / 'candidate' / 'postgres-password'}",
        "CANDIDATE_POSTGRES_PASSWORD_FILE=/tmp/postgres-password",
    )
    env_file.write_text(text)
    with pytest.raises(ValueError, match="volatile temporary storage"):
        validate(env_file)


def test_rejects_permissive_environment_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _treat_tmp_path_as_persistent(monkeypatch)
    env_file = _write_candidate_files(tmp_path / "candidate")
    env_file.chmod(0o644)
    with pytest.raises(ValueError, match="group or others"):
        validate(env_file)


def test_requires_full_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _treat_tmp_path_as_persistent(monkeypatch)
    env_file = _write_candidate_files(tmp_path / "candidate")
    env_file.write_text(env_file.read_text().replace(REVISION, "main"))
    with pytest.raises(ValueError, match="full Git revision"):
        validate(env_file)
