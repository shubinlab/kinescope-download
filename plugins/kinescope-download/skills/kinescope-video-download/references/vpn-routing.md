# Narrow Kinescope VPN routing

Use this reference only when the user explicitly asks for Kinescope traffic to bypass a VPN. Do not change VPN settings for ordinary downloads. Never disable the VPN, change its global mode, rewrite routes/firewall rules, or add ChatGPT domains to exclusions as a shortcut.

For AdGuard VPN, inspect its mode and both exclusion lists before changing anything. Resolve the installed CLI instead of assuming a platform-specific binary path:

```bash
command -v adguardvpn-cli
adguardvpn-cli status
adguardvpn-cli site-exclusions mode
adguardvpn-cli site-exclusions show --for-mode general
adguardvpn-cli site-exclusions show --for-mode selective
```

If the CLI is not on `PATH`, use only a documented path for that platform. If the installed client has no supported exclusion interface, stop before modifying routes or firewall rules. In `general` mode, add only exact Kinescope playback/CDN hostnames observed in this video's manifest. The share page host is often `kinescope.io`; inspect the manifest for redirected or segment hosts before deciding whether another hostname is needed. Do not add broad wildcard domains without evidence.

In `selective` mode, sites in the selective list go through the VPN; leave Kinescope out of that list when the user requests direct routing. Remove an exact matching Kinescope entry only if present. If a wildcard rule covers the needed host, do not remove that broader rule automatically; explain the effect and ask before changing it. Do not switch modes. For other VPN products or unclear mode semantics, inspect supported split tunneling before changing anything.

After confirming the active mode and current list, the narrow General-mode change is:

```bash
adguardvpn-cli site-exclusions add kinescope.io --for-mode general
```

Add another exact hostname only if it appears in this video's manifest or redirects. In Selective mode, remove only an exact matching entry when direct routing is requested:

```bash
adguardvpn-cli site-exclusions remove kinescope.io --for-mode selective
```

After an exclusion change, verify that the VPN is connected and a resolved ChatGPT address still routes through the VPN interface:

```bash
# Linux
chatgpt_ip=$(getent ahostsv4 chatgpt.com | awk 'NR==1 {print $1}')
ip route get "$chatgpt_ip"

# macOS
chatgpt_ip=$(dscacheutil -q host -a name chatgpt.com | awk '/ip_address/ {print $2; exit}')
route -n get "$chatgpt_ip"
```

Use `ss` to identify the active Kinescope connection and remote IP, but treat its local source address as suggestive only: policy routing or VPN interception can still send packets through TUN. To claim direct egress, capture the active Kinescope transfer on physical and TUN interfaces and confirm the video payload uses the physical path. A few initial packets through TUN may be part of SNI inspection; the bulk transfer must use the physical interface. Verify ChatGPT's resolved address routes through the VPN and, when packet capture is available, confirm the active ChatGPT request on the VPN interface.

```bash
# Linux
ip -brief address
ss -tnp state established
sudo timeout 15 tcpdump -n -i any 'host KINESCOPE_IP and tcp port 443'

# macOS: capture separately using the physical and VPN interface names from `route -n get` and `ifconfig -l`.
sudo tcpdump -n -i PHYSICAL_INTERFACE 'host KINESCOPE_IP and tcp port 443'
sudo tcpdump -n -i VPN_INTERFACE 'host KINESCOPE_IP and tcp port 443'
```

Replace `KINESCOPE_IP` with the current stream/CDN address and capture while downloading. `ip route get` alone cannot prove a domain/SNI exclusion. If privileged packet capture is unavailable, report direct routing as unverified; keep the VPN connected and do not claim the download bypassed it. A global TCP/SNI inspection setting may restart the VPN; do not toggle it automatically. If required, explain the expected restart and obtain approval before changing it.
