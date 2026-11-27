# Image builds with scoped permissions

Use `deploy/cloudbuild-agent.yaml` for executor builds. It explicitly uses
`trader-builder`, not the default Compute service account. The builder needs:

- `roles/storage.objectViewer` on the existing project Cloud Build source bucket.
- `roles/artifactregistry.writer` on the `trader` repository in `us-east1`.
- `roles/logging.logWriter` on the project, with `CLOUD_LOGGING_ONLY` build logs.

It does not need project Editor, live credentials, recovery-bucket access, or
permission to deploy/restart services. Do not grant broad roles to repair a build
failure; identify the denied operation and check whether the build should do it.
See [Google's service-account guidance](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts).

Stage Dockerfile, .dockerignore, pyproject.toml, uv.lock and tracked trader/*.py
from the exact reviewed Git revision into a new private directory. Do not upload
the workspace, state, credentials or untracked files. Submit that directory:

```sh
gcloud builds submit STAGED_DIRECTORY \
  --project=trader-observatory-20261105 --region=us-east1 \
  --config=deploy/cloudbuild-agent.yaml --substitutions=_TAG=REVIEWED_REVISION \
  --async --format=json
```

Record the returned build ID and inspect it until terminal; do not submit a
second build after an observation timeout. Require SUCCESS, record the immutable
image digest, verify source hashes in the image, and follow the existing paused
release/rollback procedure. A successful build is not a production deployment.
Dashboard builds must likewise explicitly select the scoped builder and Cloud
Logging, with their own staged context/image name; this executor config does not
build or deploy the dashboard.
