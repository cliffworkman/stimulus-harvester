# 0.2.0rc2 acceptance fix

Checkpoint 7 baseline: 19b7267c8527271ffaf97fe4c3bf551e3d4c6c50.

During human acceptance, enabled but unconfigured SearXNG and Commons providers
appeared ready for discovery and failed only afterward. The desktop now presents
unconfigured providers unchecked. Enabling either routes immediately to its required
endpoint/contact setting. Invalid input stays in the dialog; cancelling leaves the
provider unchecked. Successful setup returns to the existing query form with that
provider checked. Current queries and other pending search settings are preserved.
Configuration remains unsaved until the existing Save settings & discover action.

A modal-dialog guard prevents duplicate setup prompts and search launch during
setup. Discovery rechecks required values before saving settings or launching jobs,
so later invalid edits route directly back to configuration. No request is used to
validate setup. Existing provider backend diagnostics and scientific semantics remain
unchanged. Eleven focused regression cases cover these transitions for both providers.

This material acceptance fix advances rc1 to rc2. Original rc1 binaries are retained.
The user guide also distinguishes the installer-declared Windows 10 minimum from
the empirically tested Windows 11 host. No fresh-machine claim follows from this.

Publication remains unauthorized. The owner selected certificate signing; an
authorized certificate, final signing and post-signing hashes are pending. Native
redistribution review, fresh Windows and remaining human acceptance gates must be
reported separately and resolved before publication. No release tag is created here.
