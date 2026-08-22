"""System checks for deployment identity.

These run on every ``manage.py check``, ``migrate`` and ``runserver``, so a
misconfigured instance announces itself at startup rather than after someone
has annotated for an hour into the wrong data root.

Registered under the ``deployment`` tag, so a deployment can run just these::

    python manage.py check --tag deployment

Everything here is a *warning*, never an error, with one exception (W005): a
data root that is missing or unwritable means saves will fail outright, and
that is worth refusing to start for. The rest are situations that are legal —
several are normal in development — but that in production indicate the chain
has come apart.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register


def _looks_like_a_checkout(path: Path) -> bool:
    """True when ``path`` is the root of a *different* copy of this project."""
    return (path / ".git").exists() and (path / "backend").is_dir()


@register("deployment")
def check_data_root(app_configs, **kwargs):
    """The data root must exist, be writable, and belong to this instance."""
    issues: list = []
    checkout = Path(settings.BASE_DIR).parent.resolve()

    try:
        data_root = Path(settings.MITO_DATA_ROOT).resolve()
    except (OSError, ValueError) as exc:
        return [
            Error(
                f"MITO_DATA_ROOT cannot be resolved: {exc}",
                hint="Every in-app edit is written under this path.",
                id="deployment.E001",
            )
        ]

    if not data_root.exists():
        issues.append(
            Error(
                f"MITO_DATA_ROOT does not exist: {data_root}",
                hint=(
                    "Create it, or point MITO_DATA_ROOT at the correct tree. "
                    "Saving an annotation will fail until this resolves."
                ),
                id="deployment.E005",
            )
        )
    elif not os.access(data_root, os.W_OK):
        issues.append(
            Error(
                f"MITO_DATA_ROOT is not writable by this process: {data_root}",
                hint="Annotation saves write the working mask under this path.",
                id="deployment.E006",
            )
        )

    # The incident check. A data root sitting inside a *different* checkout of
    # this project is the exact shape of the failure that motivated this
    # module: two instances, and edits landing in the one nobody is watching.
    if data_root.exists() and not data_root.is_relative_to(checkout):
        for ancestor in [data_root, *data_root.parents]:
            if ancestor == checkout:
                break
            if _looks_like_a_checkout(ancestor):
                issues.append(
                    Warning(
                        f"MITO_DATA_ROOT points inside a different checkout of "
                        f"this project.\n"
                        f"  this checkout: {checkout}\n"
                        f"  data root:     {data_root}\n"
                        f"  owned by:      {ancestor}",
                        hint=(
                            "Two instances sharing or crossing data roots is "
                            "how edits end up in a retired deployment. If this "
                            "is deliberate (a migration), ignore it; otherwise "
                            "set MITO_DATA_ROOT to this instance's own tree."
                        ),
                        id="deployment.W002",
                    )
                )
                break

    return issues


@register("deployment")
def check_expected_identity(app_configs, **kwargs):
    """Compare this instance's identity against what the operator *expected*.

    A deployment pins what it believes it is::

        MITO_EXPECTED_CHECKOUT=/home/weidf/shenb/mito-data-studio-deploy
        MITO_EXPECTED_DATA_ROOT=/home/weidf/shenb/mito-data-studio-deploy/data
        MITO_EXPECTED_DB_NAME=mito_deploy
        MITO_EXPECTED_BIND=127.0.0.1:18188
        MITO_EXPECTED_FINGERPRINT=abc123def456

    and `manage.py check --tag deployment` then fails loudly when the running
    configuration has drifted from it. This is the check that would have caught
    the original incident before anyone annotated into the wrong tree: the
    retired instance's expectations no longer matched what it was serving.

    Every comparison is **opt-in** — an unset expectation is skipped, so a
    development checkout with no pins reports nothing. Warnings rather than
    errors: a deliberate migration may legitimately cross roots, and refusing
    to start would be worse than saying so.
    """
    from core.deployment import identity as build_identity

    info = build_identity()
    service = info["service"]
    database = info["database"]
    assert isinstance(service, dict) and isinstance(database, dict)

    comparisons = [
        ("MITO_EXPECTED_CHECKOUT", "checkout", str(info["checkout"]), "W010", True),
        ("MITO_EXPECTED_DATA_ROOT", "data root", str(info["data_root"]), "W011", True),
        ("MITO_EXPECTED_DB_NAME", "database name", str(database.get("name")), "W012", False),
        ("MITO_EXPECTED_BIND", "service bind", str(service.get("bind")), "W013", False),
        ("MITO_EXPECTED_FINGERPRINT", "fingerprint", str(info["fingerprint"]), "W014", False),
    ]

    issues: list = []
    for env_name, label, actual, code, is_path in comparisons:
        expected = os.getenv(env_name)
        if not expected:
            continue
        # Paths are compared resolved so /a/./b and /a/b agree, and so a
        # trailing slash is not reported as a mismatch.
        if is_path:
            same = Path(expected).resolve() == Path(actual).resolve()
        else:
            same = expected.strip() == actual.strip()
        if not same:
            issues.append(
                Warning(
                    f"Deployment identity mismatch: expected {label} does not "
                    f"match the running configuration.\n"
                    f"  expected ({env_name}): {expected}\n"
                    f"  actual:                {actual}",
                    hint=(
                        "This instance is not the one the operator pinned. Edits "
                        "would land somewhere other than intended — resolve "
                        "before serving traffic."
                    ),
                    id=f"deployment.{code}",
                )
            )

    # A deployment that pins nothing at all cannot drift-check itself. Say so
    # once, quietly, rather than per field.
    if not any(os.getenv(name) for name, *_ in comparisons):
        if os.getenv("MITO_SERVICE_BIND"):
            issues.append(
                Warning(
                    "This instance declares MITO_SERVICE_BIND but pins no "
                    "MITO_EXPECTED_* values, so `check --tag deployment` cannot "
                    "detect identity drift.",
                    hint=(
                        "Pin MITO_EXPECTED_CHECKOUT / _DATA_ROOT / _DB_NAME / "
                        "_BIND (and optionally _FINGERPRINT) in the deployment's "
                        ".env. See docs/deployment.md."
                    ),
                    id="deployment.W015",
                )
            )

    return issues


@register("deployment")
def check_public_exposure(app_configs, **kwargs):
    """Settings that only matter once an instance is reachable publicly."""
    issues: list = []
    hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "[::1]")]

    if settings.DEBUG and hosts:
        issues.append(
            Warning(
                "DEBUG is on while ALLOWED_HOSTS names non-local hosts: "
                f"{', '.join(hosts)}",
                hint="DEBUG must be off on anything reachable from outside.",
                id="deployment.W003",
            )
        )

    if hosts and "*" in settings.ALLOWED_HOSTS:
        issues.append(
            Warning(
                "ALLOWED_HOSTS contains '*', which disables host validation.",
                hint="Name the hosts this deployment actually serves.",
                id="deployment.W004",
            )
        )

    if (
        hosts
        and getattr(settings, "MITO_ALLOW_DEV_RESET", False)
        and not settings.ENABLE_MOCK_DEV_LOGIN
    ):
        issues.append(
            Error(
                "MITO_ALLOW_DEV_RESET is enabled without development accounts.",
                hint=(
                    "Enable it only together with the explicitly configured, "
                    "disposable Development accounts surface."
                ),
                id="deployment.E010",
            )
        )

    return issues


@register("deployment")
def check_upgrade_feature_dependencies(app_configs, **kwargs):
    """Reject partial upgrade combinations that cannot work end to end.

    Individual switches remain useful as emergency rollback controls, but a
    deployment must not advertise a frontend data path whose backend is inert,
    or enable a writer without the operation log it relies on for recovery.
    """
    issues: list = []

    def require(enabled: bool, dependency: bool, feature: str, needed: str, code: str):
        if enabled and not dependency:
            issues.append(
                Error(
                    f"{feature} is enabled but {needed} is disabled.",
                    hint=(
                        f"Enable {needed}, or disable {feature}. Use "
                        "MITO_UPGRADE_PROFILE=webknossos for the coherent full stack."
                    ),
                    id=f"deployment.{code}",
                )
            )

    require(
        settings.FEATURE_CHUNK_SERVICE,
        settings.FEATURE_VOLUME_PYRAMIDS,
        "FEATURE_CHUNK_SERVICE",
        "FEATURE_VOLUME_PYRAMIDS",
        "E023",
    )

    pull_queue = os.getenv("VITE_FEATURE_CHUNK_PULL_QUEUE", "false").lower() in {
        "1", "true", "yes", "on"
    }
    renderer = os.getenv("VITE_FEATURE_CHUNK_RENDERER", "false").lower() in {
        "1", "true", "yes", "on"
    }
    require(
        pull_queue,
        settings.FEATURE_CHUNK_SERVICE,
        "VITE_FEATURE_CHUNK_PULL_QUEUE",
        "FEATURE_CHUNK_SERVICE",
        "E024",
    )
    require(
        renderer,
        pull_queue,
        "VITE_FEATURE_CHUNK_RENDERER",
        "VITE_FEATURE_CHUNK_PULL_QUEUE",
        "E025",
    )

    if settings.MITO_UPGRADE_PROFILE == "webknossos":
        feature_names = (
            "FEATURE_TEAMS",
            "FEATURE_AUTO_FILL_SCHEDULER",
            "FEATURE_REVIEW_HISTORY",
            "FEATURE_DASHBOARDS",
            "FEATURE_ANNOTATION_OPS",
            "FEATURE_INTERPOLATION",
            "FEATURE_ANNOTATION_TOOLS",
            "FEATURE_VOLUME_PYRAMIDS",
            "FEATURE_CHUNK_SERVICE",
        )
        disabled = [name for name in feature_names if not getattr(settings, name)]
        if disabled:
            issues.append(
                Warning(
                    "The webknossos profile is active but backend upgrade features "
                    f"are explicitly disabled: {', '.join(disabled)}.",
                    hint=(
                        "Apply ops/staging/upgrade.env.example for a full candidate. "
                        "Keep overrides only for a documented emergency rollback."
                    ),
                    id="deployment.W021",
                )
            )
        engine = settings.DATABASES["default"]["ENGINE"]
        if not engine.endswith("postgresql"):
            issues.append(
                Warning(
                    "The webknossos upgrade profile is running without PostgreSQL.",
                    hint=(
                        "Use PostgreSQL for real deployments; scheduler concurrency "
                        "guarantees are validated against PostgreSQL."
                    ),
                    id="deployment.W020",
                )
            )

    if settings.MITO_UPGRADE_PROFILE == "production_integrated_v1":
        expected = settings.PRODUCTION_INTEGRATED_FEATURES
        # Pyramids/chunks are the only production features with a documented
        # four-switch emergency rollback. All four must be disabled together;
        # the dependency checks above reject partial combinations.
        rollback_features = {"FEATURE_VOLUME_PYRAMIDS", "FEATURE_CHUNK_SERVICE"}
        drift = [
            name
            for name, enabled in expected.items()
            if name not in rollback_features
            and bool(getattr(settings, name)) is not enabled
        ]
        if drift:
            issues.append(
                Error(
                    "The production_integrated_v1 feature contract was overridden: "
                    + ", ".join(drift),
                    hint=(
                        "Remove individual FEATURE_* overrides. Roll back by selecting "
                        "the legacy profile or the retained v1.0.0 service, not by "
                        "silently mutating the audited production profile."
                    ),
                    id="deployment.E026",
                )
            )
        backend_streaming = bool(
            settings.FEATURE_VOLUME_PYRAMIDS and settings.FEATURE_CHUNK_SERVICE
        )
        if backend_streaming and not (pull_queue and renderer):
            issues.append(
                Error(
                    "production_integrated_v1 requires PullQueue and the chunk renderer on.",
                    hint="Set both VITE feature declarations to true and rebuild the SPA.",
                    id="deployment.E027",
                )
            )
        if not backend_streaming and not pull_queue and not renderer:
            issues.append(
                Warning(
                    "The production chunk transport emergency rollback is active.",
                    hint=(
                        "Restore both backend flags and both Vite declarations to true "
                        "after the incident is resolved."
                    ),
                    id="deployment.W022",
                )
            )
        engine = settings.DATABASES["default"]["ENGINE"]
        if not engine.endswith("postgresql"):
            issues.append(
                Error(
                    "production_integrated_v1 requires PostgreSQL.",
                    id="deployment.E028",
                )
            )
        if not settings.MITO_METRICS_BEARER_TOKEN:
            issues.append(
                Error(
                    "production_integrated_v1 requires an authenticated metrics token.",
                    hint="Set MITO_METRICS_BEARER_TOKEN in the protected environment.",
                    id="deployment.E029",
                )
            )
        runtime_contract = {
            "MITO_QC_PROVIDER": "basic",
            "MITO_VISUALIZATION_PROVIDER": "inapp",
            "MITO_PUBLISHING_PROVIDER": "placeholder",
            "MITO_TRACKING_PROVIDER": "sam2",
            "MITO_SAM2_CUDA_DEVICE": 0,
            "MITO_PROCESSING_BACKEND": "local",
            "MITO_LOCAL_EXECUTABLE_ALLOWLIST": "",
            "MITO_AI_CUDA_DEVICE": "1",
        }
        runtime_drift = [
            f"{name}={getattr(settings, name)!r} (expected {expected!r})"
            for name, expected in runtime_contract.items()
            if getattr(settings, name) != expected
        ]
        if not settings.MITO_AI_ONNX_CUDA:
            runtime_drift.append("MITO_AI_ONNX_CUDA=False (expected True)")
        if settings.MITO_PROCESSING_ENV_ALLOWLIST:
            runtime_drift.append(
                "MITO_PROCESSING_ENV_ALLOWLIST is not empty (expected no external job env)"
            )
        if runtime_drift:
            issues.append(
                Error(
                    "production_integrated_v1 runtime contract drift: "
                    + "; ".join(runtime_drift),
                    hint=(
                        "Use the audited v1.1 environment template. SAM2 uses CUDA "
                        "device 0 and EfficientSAM ONNX uses device 1; nnU-Net/Slurm "
                        "and arbitrary local executables remain disabled."
                    ),
                    id="deployment.E030",
                )
            )

        required_assets = {
            Path(settings.MITO_CELLABLE_MODELS_ROOT) / "efficient_sam_vits_encoder.onnx": 89_558_337,
            Path(settings.MITO_CELLABLE_MODELS_ROOT) / "efficient_sam_vits_decoder.onnx": 16_565_728,
            Path(settings.MITO_SAM2_CHECKPOINT): 898_083_611,
        }
        invalid_assets = [
            f"{path} (expected {size} bytes)"
            for path, size in required_assets.items()
            if not path.is_file() or path.stat().st_size != size
        ]
        if invalid_assets:
            issues.append(
                Error(
                    "production_integrated_v1 model assets are missing or wrong-sized: "
                    + "; ".join(invalid_assets),
                    hint="Install the three hash-verified offline LFS assets before startup.",
                    id="deployment.E031",
                )
            )

    return issues
