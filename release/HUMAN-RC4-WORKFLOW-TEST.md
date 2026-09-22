# Human rc4 workflow test

Use **0.2.0rc4**. Extract the entire portable ZIP into a new folder and launch
**Stimulus Harvester.exe**, or use the rc4 installer after closing/uninstalling the
previous installed candidate. Keep projects outside the application folder; back up
an existing project before opening it. The portable option keeps prior application
folders separate. No ML or detector setup is required.

These Windows builds are unsigned. Windows may show Unknown Publisher or SmartScreen
warnings. Signing is intentionally deferred across the Stimulus Modernization Suite;
SHA-256 checks identify bytes, not a publisher.

A. Create a test project or open a backed-up test copy of an existing project.
B. Search for `prison cell` and `prison interior`; optionally try `church interior`
   and `chapel interior`. To exercise the Openverse boundary, use 20 results/page and
   at least 13 pages. Keep Commons small initially (one page, a few results).
C. Use Openverse and Commons where configured. Checking unconfigured Commons should
   immediately ask for your operator email or project URL. Leave SearXNG unchecked
   unless you have a configured endpoint.
D. Choose **Start a new search**, then **Download pending images**. Check whether the
   found / downloaded / failed / pending summary makes sense. Unique image files can
   be fewer than downloaded results when sources share the same bytes.
E. If Openverse reports the 240-result anonymous boundary, expect normal completion
   and a limit explanation, not a generic HTTP 401 failure. A query with fewer results
   can end earlier. Starting another search begins at page 1; this is not resume.
F. Check that some Commons images download, or that failures have understandable
   status. Stop and record unexplained errors; do not repeatedly hammer denied hosts.
G. If eligible failures exist, use **Retry failed downloads**. No failures is also a
   valid outcome; you do not need to manufacture a failure. Server wait periods may
   temporarily make retry unavailable.
H. Confirm previously successful downloads are not repeated and the counts update
   sensibly. Earlier failure evidence should remain available after a successful retry.
I. Continue into the current results/review interface. **Stop at the first point that
   becomes cumbersome, confusing or unclear.** Describe what you tried, what you
   expected, what happened, and what you needed next. A screenshot is useful.

Reply with your Windows version, installer or portable, queries/providers used,
before/after counts if you retried, and the first major usability bottleneck.
You do **not** need to complete Screen, Freeze, Assemble or Export for this test.

This is exploratory human workflow feedback, not final public release authorization.
