# Upstream research / provenance

Agent B researched the following exact upstream pin before implementation:

- Repository: `wzyn20051216/solidworks-automation-skill`
- Commit: `5287d2e3d100dedb10e94910523f294a46176c54`
- License at that exact pin: MIT
- Relevant upstream file reviewed: `scripts/sw_connect.py`

The lane reuses/adapts behavioral ideas rather than importing the upstream MCP server or Python modules wholesale. The specific ideas carried forward are:

- year-to-ProgID mapping for SolidWorks automation;
- attach-first versus provider-started application ownership distinction;
- provider cleanup must only exit an application it owns;
- `OpenDoc6` by-ref error/warning handling, including late-bound fallback for pywin32 COM proxy differences;
- native save/save-as error/warning capture;
- COM member access that tolerates property/method representation differences.

No upstream arbitrary script/macro invocation surface is imported.

MIT notice from the exact pin:

> Copyright (c) 2026 SolidWorks Automation Skill Contributors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
