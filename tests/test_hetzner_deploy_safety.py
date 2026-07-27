from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hetzner_deploy_uses_immutable_release_worktrees():
    deploy = (ROOT / "deploy" / "hetzner" / "deploy.sh").read_text(encoding="utf-8")

    assert 'worktree add --detach "${RELEASE_DIR}" "${TARGET_COMMIT}"' in deploy
    assert 'RELEASES_DIR="${RELEASES_DIR:-/opt/mobiliti-worker/releases}"' in deploy
    assert 'WORKER_IMAGE_TAG="${TARGET_COMMIT}"' in deploy
    assert "git reset --hard" not in deploy
    assert "rm -rf" not in deploy


def test_hetzner_bootstrap_refuses_to_replace_existing_non_git_directory():
    bootstrap = (ROOT / "deploy" / "hetzner" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )

    assert "Refusing to replace non-git path" in bootstrap
    assert "git reset --hard" not in bootstrap
    assert "rm -rf" not in bootstrap
