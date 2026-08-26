# Calendar and reply-state forward queue - 2026-08-26

1. Push the verified feature branch and open a non-draft PR with exact checks and live proof.
2. Keep AGENTIC-2527 and AGENTIC-2528 in progress until the PR is merged and release state is verified.
3. Await explicit Cayman merge approval. Do not merge from this work session without that approval.
4. After merge, rerun `bash tools/gates/source-release-gate.sh` on clean `main`; its stamp must bind the merge commit.
5. Create and push signed tag `v3.12.1`, publish the GitHub Release with `.zip`, `.plugin`, and `.mcpb`, then run the marketplace handoff.
6. Promote and verify the installed plugin separately. The currently installed 3.12.0 runtime is not proof of 3.12.1 delivery.
7. Optional follow-up: repeat EventKit source-metadata acceptance on a host where the optional EventKit dependency is installed and full access is already granted.
