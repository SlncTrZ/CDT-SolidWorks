"""SolidWorks runtime boundary (migration W1/W2).

Provider-facing seam between ``IntegratedProviderRuntime`` and the Windows
STA COM session. ``SolidWorksRuntimePort`` declares the seam; ``LocalSessionAdapter``
reuses the existing ``SolidWorksSession`` 1:1 (no COM code moved).
"""

from cdt_solidworks.runtime.local_adapter import LocalSessionAdapter
from cdt_solidworks.runtime.port import SolidWorksRuntimePort
from cdt_solidworks.runtime.remote_adapter import RemoteSessionAdapter
from cdt_solidworks.runtime.transport import (
    LocalSolidWorksTransport,
    RemoteSolidWorksTransport,
    SolidWorksRuntimeTransport,
)
from cdt_solidworks.runtime.workstation_agent import (
    WorkstationAgentConfig,
    WorkstationSolidWorksRuntimeAgent,
)

__all__ = [
    "LocalSessionAdapter",
    "LocalSolidWorksTransport",
    "RemoteSessionAdapter",
    "RemoteSolidWorksTransport",
    "SolidWorksRuntimePort",
    "SolidWorksRuntimeTransport",
    "WorkstationAgentConfig",
    "WorkstationSolidWorksRuntimeAgent",
]
