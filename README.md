# CDT-SolidWorks

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](pyproject.toml)
[![MCP Protocol](https://img.shields.io/badge/MCP-Standard-orange.svg)](https://modelcontextprotocol.io)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%20x64-lightgrey.svg)](docs/TOOL_GUIDE.md)
[![Tools](https://img.shields.io/badge/tools-149%20callable-green.svg)](docs/TOOL_GUIDE.md)

Typed SOLIDWORKS MCP execution provider for native documents, sketches, features,
assemblies, drawings and exchange operations. Engineering design decisions,
standards and professional approval belong to CDT_Engineer.

Provider `0.1.2` · independent contract `0.1.0` · 149 public tools.
Measured native target: SOLIDWORKS 2024 SP0.1 on Windows.
Other versions need separate acceptance. Broad feature families remain partial;
use the granular runtime capability map rather than inferring full suite coverage.

---

## Quickstart / MCP Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cdt-solidworks": {
      "command": "uvx",
      "args": ["cdt-solidworks"]
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "cdt-solidworks": {
      "command": "uvx",
      "args": ["cdt-solidworks"]
    }
  }
}
```

---

## Install and start

```powershell
python -m pip install .
cdt-solidworks
```

Before startup, provision deployment-managed authentication, the independent
topology-reference signing secret, issuer/resource URLs and allowed file roots
as specified in the [runtime guide](docs/TOOL_GUIDE.md#launch).
Missing configuration fails closed; no configured file roots means CAD path
operations remain disabled. Native calls require a ready Windows STA/COM session.

## Use safely

Call `help`, `system_status`, `system_capabilities` and `application_probe`
before native execution. Pin the contract hash and application/source identity.
Provider-owned and user-owned sessions remain distinct.

- A COM return value is insufficient; verify rebuild/error state and postconditions.
- Native dispatch is serialized through one STA apartment.
- An in-flight timeout remains uncertain until reconciliation.
- Stop/drain must protect dirty documents and owned work.
- No arbitrary macro/script or dynamic COM invocation surface is exposed.

## Reference

- [Release, installation and rollback](docs/RELEASE_AND_DEPLOYMENT.md).
- [Tool contract and capability limits](docs/TOOL_GUIDE.md).
- [Pinned shared specification](docs/SPEC_BASELINE.md).
- [Contributor tests](tests/README.md).
- [Lifecycle ownership](https://github.com/SlncTrZ/CDT_Engineer/blob/main/docs/EXECUTION_LIFECYCLE_CONTRACT.md).

Native acceptance retains its original tested source/runtime scope.
Hosted CI or a source snapshot does not certify a new native deployment.

## License

Distributed under the open-source MIT License. See [LICENSE](LICENSE).
