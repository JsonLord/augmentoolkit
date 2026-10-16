"""Fail closed before egress. Client options can only restrict server policy."""

from .schemas import SafeError

D2_PROVIDERS = frozenset({"helmholtz", "blablador", "instituteforx"})


def sensitivity(requested, minimum):
    return max(requested, minimum, key=lambda x: ("D0", "D1", "D2").index(x))


def authorize(backend, options, settings, caps):
    level = options.sensitivity
    if not caps.text or not caps.multimodal_verified:
        raise SafeError("backend_unqualified")
    if backend.name == "cpu":
        if backend.external and (
            not options.allow_external_cpu or level not in settings.external_cpu_classes
        ):
            raise SafeError("no_authorized_destination")
        if backend.external and level != "D0":
            # No remote D1/D2 approval protocol is implemented; configuration alone is insufficient.
            raise SafeError("no_authorized_destination")
        return
    if not settings.gpu_enabled:
        raise SafeError("gpu_disabled")
    # No existing FreeLLMAPI destination enforcement contract was available for audit.
    # D2 stays hard-disabled, including hidden retry/fallback and caller overrides.
    if level != "D0":
        raise SafeError("no_authorized_destination")
    if not backend.qualified:
        raise SafeError("backend_unqualified")
