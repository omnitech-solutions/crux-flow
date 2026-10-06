"""Hostile fixture — a target whose TOP-LEVEL package is literally named
``introspect``.

If the confined child loaded its TRUSTED introspection module by bare
``import introspect`` while the target clone sits on ``sys.path[0]``, THIS
package would shadow it and run its import-time code UNCONFINED — before the
network cut, the resource rlimits, and the audit deny-list are installed. The
child loads the trusted module by ABSOLUTE FILE PATH precisely so this cannot
happen (ADR-0075 decision 2).

The package exposes a non-app object and NO ``introspect_app`` function, so the
two code paths are behaviorally distinguishable: the trusted introspector
rejects the object with an honest no-capture ("neither an app instance nor a
callable factory"), whereas a shadow would make the child call a missing
``introspect_app`` and fail with an ``AttributeError``.
"""

# A marker proving THIS module's import-time code ran. Harmless on its own; the
# test keys off the trusted-vs-shadow behavioral difference, not this value.
SHADOW_IMPORTED = True

app = object()  # not a FastAPI/Flask app and not callable
