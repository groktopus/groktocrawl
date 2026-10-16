# Publish stable and versioned release images

After release-please creates a published stable release and **Docker Build &
Publish** succeeds on `main` at that release commit, a maintainer can dispatch
**Promote Release Images** from `main`. Enter the existing tag, such as
`v0.15.0`. The workflow uses its package-scoped GitHub token, so no personal
access token or replacement Git tag is needed.

The job verifies all ten published images before writing any image tag. It
requires both `linux/amd64` and `linux/arm64`, matching OCI source/revision
labels, and the agent's baked source revision. It copies the original index
bytes to `stable`, major, minor, patch, and the exact release tag, then checks
that every resulting digest is unchanged. It downloads no image layers and
performs no rebuild or deployment.

Download the `release-image-provenance` artifact for the source workflow,
commit, platform manifests, and published index digests. A failed gate writes
no tags. If a registry write fails partway through publication, rerun the same
release after resolving the registry error; copying the same index is
idempotent. A local verification-only run is available with:

```sh
python scripts/promote_release_images.py --release-tag v0.15.0
```
