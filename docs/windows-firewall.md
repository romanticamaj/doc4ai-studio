# Windows Firewall

On `127.0.0.1` (the default) Windows Firewall is not involved: it never prompts for or blocks loopback traffic, and
no token is needed.

Binding a network address (`--host 0.0.0.0`) makes Windows ask whether `python.exe` may accept connections. The rule
it creates names the full path of that executable, and the Python that `uv run` uses lives in a versioned folder
(e.g. `%APPDATA%\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe`): every Python upgrade is a new program to
the firewall and asks again. Dismissing the prompt (Cancel, or no network type ticked) silently creates a **Block**
rule, after which the server is unreachable from other devices with no further prompt.

Allow the port instead of the program, once, in an elevated PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Doc4AI 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

- Use the port you serve on (`--port`; the default is 8765).
- `-Profile Private` keeps the port closed on networks Windows treats as Public (cafés, hotel Wi-Fi). A Tailscale
  interface must be set to Private for the rule to apply to it.
- Remove Block rules left by a dismissed prompt:

  ```powershell
  Get-NetFirewallRule -Direction Inbound | Where-Object { $_.DisplayName -eq 'python.exe' -and $_.Action -eq 'Block' } | Remove-NetFirewallRule
  ```

- Remove the port rule when you go back to `127.0.0.1`: `Remove-NetFirewallRule -DisplayName "Doc4AI 8765"`.
