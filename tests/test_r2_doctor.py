import hashlib
import importlib.util
from pathlib import Path


def _load_r2_doctor():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "r2_doctor.py"
    spec = importlib.util.spec_from_file_location("r2_doctor_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r2_doctor = _load_r2_doctor()


def test_load_cloudflare_env_file_extracts_expected_labels(tmp_path):
    source = tmp_path / "cloudflare.txt"
    source.write_text("ACCOUNT ID:abc123\nAPI TOKEN :tok_456\n", encoding="utf-8")

    values = r2_doctor._load_kv_file(source)

    assert values == {"R2_ACCOUNT_ID": "abc123", "CLOUDFLARE_API_TOKEN": "tok_456"}


def test_derive_r2_credentials_uses_token_id_and_sha256_secret():
    derived = r2_doctor.derive_r2_s3_credentials("secret-token-value", "token-id-123")

    assert derived == ("token-id-123", hashlib.sha256(b"secret-token-value").hexdigest())


def test_evaluate_cors_rules_requires_origin_methods_and_content_type():
    rules = [
        {
            "AllowedOrigins": ["https://web-lemon-one-45.vercel.app"],
            "AllowedMethods": ["GET", "PUT", "HEAD"],
            "AllowedHeaders": ["Content-Type"],
        }
    ]

    result = r2_doctor.evaluate_cors_rules(rules, ["https://web-lemon-one-45.vercel.app"])

    assert result["cors_ready"] is True
    assert result["cors_missing"] == []


def test_evaluate_cors_rules_reports_missing_origin():
    rules = [
        {
            "AllowedOrigins": ["https://other.example"],
            "AllowedMethods": ["GET", "PUT", "HEAD"],
            "AllowedHeaders": ["*"],
        }
    ]

    result = r2_doctor.evaluate_cors_rules(rules, ["https://web-lemon-one-45.vercel.app"])

    assert result["cors_ready"] is False
    assert result["cors_missing"] == ["https://web-lemon-one-45.vercel.app"]
