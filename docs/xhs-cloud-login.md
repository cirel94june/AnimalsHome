# Cloud login helper (prepared; not enabled)

The existing Xiaohongshu reader remains responsible for reading feeds, search,
notes and comments. This helper adds owner-operated QR login and server-side
session refresh. It does not install or expose the third-party all-purpose MCP.
No posting, commenting, liking, following, messaging, or arbitrary browser
operation is available through the helper.

## Resource and rollout gates

This feature is not enabled by pushing application code. Before installation:

1. Confirm the shared server has sufficient spare memory without stopping other
   applications. The reviewed prerequisite installer refuses below 300 MiB
   available memory; each browser launch additionally requires 350 MiB.
2. Run the reviewed anonymous browser benchmark in its dedicated bounded systemd
   unit. Keep Chrome's sandbox enabled. Check the main application remains healthy.
3. Independently review the final install plan and pinned source commit, back up,
   then install official Chrome/Xvfb and an isolated environment from
   `deploy/linux/xhs-browser-requirements.txt` into `/opt/xhs-browser/venv`.
   Do not modify the application's Python environment.
4. Create the unprivileged `xhs-browser` system user, private home/profile at
   `/var/lib/xhs-browser` (0700), root-owned helper source at
   `/opt/xhs-browser/xhs_browser_service.py`, and the provided systemd unit.
   Source must be from the reviewed Git commit, not locally patched production.
5. Start the helper and test its private Unix socket as the application user.
   Deploy the reviewed application commit through its normal backup/health/
   rollback flow. Do not expose an additional HTTP or browser debugging port.
6. Test the owner QR flow and actual account validation, restart the helper, and
   verify persisted login and a read-only feed/detail request. Do not create test
   chat messages or publish platform content. A working unit test is not evidence
   of platform login or successful real browsing.

The unit caps the complete helper process tree at 360 MiB memory, 128 MiB swap,
60% CPU. Browsers close after idle use; login windows expire after five minutes.
The helper communicates over `/run/xhs-browser/api.sock`, owned by the helper
user and application group (0660), inside a 0750 runtime directory. It launches
Chrome over a pipe, without a CDP listening port. The profile and accepted session
stay on the server. No credentials or QR frames belong in logs or Git.

## Owner flow

In the existing Xiaohongshu page, open basic settings and select QR login.
Scan using the intended account and complete any platform verification on the
phone. Then select "I confirmed on my phone". The application validates the
platform account and only then replaces its previous credentials. Existing
enable/automatic/actor switches are preserved. Cloud mode always disables writes.

Credentials are refreshed from the persisted profile at most every four hours
on demand, with the application checking the account ID before accepting changed
credentials. A different account requires explicit confirmation again. Platform
revocation, phone verification or risk checks can still require another scan;
no CAPTCHA or platform verification is bypassed.

The UI uses same-origin POSTs with a custom header and the existing whole-site
authentication door. Raw credentials are never proxied to the browser or model.
The old manual Cookie input remains available as a compatibility option; using
it explicitly exits cloud mode. Disabling the helper hides its entry point and
does not silently fall back to a previous account.

## Rollback

Stop and disable only `xhs-browser.service`, restore the previously deployed
application commit through the usual rollback, and preserve the private profile
until the owner decides to delete it. Never stop unrelated services to make
memory room. Do not install on an insufficiently provisioned host.
