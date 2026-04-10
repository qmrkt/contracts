from __future__ import annotations

from hypothesis import HealthCheck, settings


COMMON_SETTINGS = dict(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.register_profile("default", settings(max_examples=100, **COMMON_SETTINGS))
settings.register_profile("dev", settings(max_examples=100, **COMMON_SETTINGS))
settings.register_profile("ci", settings(max_examples=500, **COMMON_SETTINGS))
settings.register_profile("thorough", settings(max_examples=5_000, **COMMON_SETTINGS))
