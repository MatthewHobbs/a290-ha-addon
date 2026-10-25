# Decision records

Read in numeric order. Each records a decision that was made, not a plan.

| # | Decision |
| --- | --- |
| 0001 | The r5 twin is derived from this repo, and parity is a check with a written list of allowed differences. |
| 0002 | Every release is installed, configured and run under a real stable Supervisor, on both arches at both ends of the Core range, before it ships. |
| 0003 | The UI gate waits for every pop-up card before scanning, rather than scanning on a timer. |
| 0004 | Bubble button names wrap instead of scrolling. |
| 0005 | Release publishing waits for the UI gate, and its check always reports. |
| 0006 | Releases are cut on request, and the bump PR publishes the image before the merge. |
