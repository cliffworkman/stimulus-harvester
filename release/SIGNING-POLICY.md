# Checkpoint 8 signing policy — 2026-09-21

Stimulus Harvester's current Windows builds are unsigned. Windows may display an Unknown Publisher or SmartScreen warning. Code signing is intentionally deferred while distribution infrastructure is being reconciled across the Stimulus Modernization Suite.

Unsigned distribution is explicitly authorized for this development/portfolio release stage. Signing remains planned future suite-level work and is not a Checkpoint 8 acceptance blocker.

Final public release authorization still applies to the exact unsigned bytes, hashes, documentation and destination. No tag, upload, publication or announcement is authorized by this policy decision.

SHA-256 checks establish byte identity against the recorded manifest; they do not
establish publisher identity, Windows trust or publisher authentication.

The three applications in the Stimulus Modernization Suite still need distribution
alignment across installers, publisher identity, signing, icons, versioning and release
infrastructure. This is an intentional product-scope decision. Do not obtain, generate,
request or wait for a certificate for this stage, and do not change the binaries to
support signing. Existing build hooks and future signing instructions are retained.

All non-signing acceptance gates remain in force. Current open gates are genuine
fresh-Windows validation, remaining human usability/accessibility acceptance and native
redistribution clearance. Final publication authorization and destination follow those
gates. An unsigned release recommendation is possible only after the non-signing gates
are satisfied.

This policy supersedes earlier Checkpoint 8 certificate-supply or mandatory-signing
language, including historical source archives and embedded documentation retained as
byte-identical evidence. It does not rewrite those archives. The rc2 binaries remain
built from c0136530a4059a8e7bfd52dc361fc48926cc8faf. Current companion documentation
has been revised on 2026-09-21 without rebuilding or repackaging the application.
