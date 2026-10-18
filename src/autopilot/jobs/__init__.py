"""Entry points for the GitHub Actions jobs (``python -m autopilot.jobs <job> ...``).

Each job module imports only what that job may use: ``publish`` and ``renew`` run with secrets,
so they and everything they import use the standard library plus PyYAML (and PyNaCl for renew),
never the render stack.
"""
