"""Explicit standby selection for bounded repair and independent review Agents."""
import os
if os.environ.get('CODEX_LUNA_ADAPTER') == '1':
    import luna_sandbox
    luna_sandbox.install()
