# rc6 collection milestone verification

The application build source is `60782aff2498e7ef78da4d2334675b71166f930d`.
Subsequent verification-only changes do not alter the installer, portable ZIP,
wheel or their packaged application source. The historical `baseline_commit`
in release.json is the original packaging baseline, not this build's source commit.

The ordinary suite passed 390 pytest cases (two dependency deprecation warnings),
including 26 collection cases. The release-build suite passed nine cases. The
final collection-focused rerun passed the same 26 cases; do not add repeat runs
or native harness groups to the distinct pytest case counts.

Eight distribution harness groups passed, including 13 nested installed-runtime
groups. They cover per-user install, both console entry points, actual GUI launcher,
isolated no-ML startup, Unicode/spaces project paths, native synthetic workflow,
historical CP6 compatibility/reassembly, browser resources, a bounded two-result
Openverse discovery, portable startup and uninstall/reinstall/reopen/removal with
external project evidence unchanged. The nested groups are not additional pytest
cases. Live discovery success is not a claim that those image bytes were acquired.

The packaged collections harness passed six groups on the native Windows Qt
platform: additive migration without inherited review; reused observations with
independent policy/override; conflicting collection reviews and GUI-created v2
freezes; light/dark layout at 850x690 and 1260x940 at 100% display scaling; v2
assembly/export after live edits and unchanged v1 reassembly; and 1,629 persisted
candidates with two overlapping collections, pending/failed members and shared
canonical bytes. Scale assignment took 2.14 seconds; collection refresh took
16.14 seconds. This is evidence of correctness, not a responsiveness target met.

The first native scale run reached five groups then exceeded the unchanged
90-second worker deadline. Repeated QTest.qWait calls starved the Python worker
on this host. The verifier now dispatches events and explicitly releases the GIL
between polls. A diagnostic then opened the fixture in 25 seconds and switched
collection in 13 seconds. The final packaged run passed all six groups using this
corrected harness. No application behavior or assertion was relaxed. The packaged
build-recipe retains the original harness; use the corrected repository verifier
for reproducing this milestone's native scale result.

The first packaging attempt failed when a long working directory combined with
deep Qt attribution paths exceeded the Inno compiler's usable path length. A
shorter work root completed the locked build. The failed attempt was retained,
and its application wheel is byte-identical to the successful wheel. No previous
release artifact was overwritten. Build roots should remain short.

All 3,751 portable payload files matched their recorded hashes. No checked local
workspace/user path leakage was found. Two broad keyword matches occur inside
third-party RECORD hash values, not paths. GUI and CLI PE subsystems are 2 and 3.
Prior rc2–rc5 manifests account for 1,213 unchanged files; seven historical
freeze/assembly/export artifacts independently verified and remained unchanged.

These are local scripted checks, not fresh-Windows or human accessibility
certification. The requested private project's former path was absent during the
final recheck; no current verification of that project is claimed. It was not
modified by this work. The earlier read-only audit remains historical evidence.

Windows builds remain intentionally unsigned. Windows may show Unknown Publisher
or SmartScreen warnings. Code signing remains future suite-level work. Hashes
establish byte identity, not publisher identity. No publication or tag is authorized.
