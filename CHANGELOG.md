# CHANGELOG

<!-- version list -->

## v1.5.0 (2026-07-27)

### Bug Fixes

- Update callback URL assertion in tests and remove unnecessary log startup patch
  ([`a4d5f20`](https://github.com/javadib/jiffy_gateway/commit/a4d5f20cb7df16502f1afd02589c162b6bd0b76a))

- Update request headers in tests to align with new header structure and bump version to 1.4.0
  ([`d16589e`](https://github.com/javadib/jiffy_gateway/commit/d16589eb82fffd3ff7258450b252f464aadfdbbf))


## v1.4.0 (2026-07-26)

### Bug Fixes

- Ensure data directory is created before collecting static files
  ([`6f20004`](https://github.com/javadib/jiffy_gateway/commit/6f200044412e01b868e2b753386a68590a6c314d))

- Remove unnecessary directory creation in Dockerfile
  ([`becd22d`](https://github.com/javadib/jiffy_gateway/commit/becd22d500d4290c5133b9be3351c0394af769dd))

- Update Jiffy Dispatch workflow and increase callback secret length
  ([`478be34`](https://github.com/javadib/jiffy_gateway/commit/478be3433da85ed1a70ca4e28369a58a8450ff9c))

### Features

- Add Jiffy Dispatch workflow for issue handling and ingestion
  ([`69d6094`](https://github.com/javadib/jiffy_gateway/commit/69d60943ed2c7b8e809450b9b0dd01a0bdd04729))

- Increase max length of callback_secret to 1024 in Task model and serializers
  ([`80405f4`](https://github.com/javadib/jiffy_gateway/commit/80405f435753e6b613be903a4648c955e3a5326a))

- Update callback handling to pass secret as opaque value and increase callback_secret length
  ([`95463af`](https://github.com/javadib/jiffy_gateway/commit/95463af275e4afc02081972938f0592f3d1d4161))

- Update Docker commands to use 'uv run' and add migration for callback_url field
  ([`b461df6`](https://github.com/javadib/jiffy_gateway/commit/b461df636ed4fd7974617827a977429b3d45f859))

### Refactoring

- Comment out unused network configuration in sandbox container setup
  ([`461d5ad`](https://github.com/javadib/jiffy_gateway/commit/461d5ada2181f46815ba20b47dcefe0e9725549d))


## v1.3.2 (2026-07-26)

### Bug Fixes

- Update Docker publish workflow to use GITHUB_TOKEN for authentication
  ([`2fa070d`](https://github.com/javadib/jiffy_gateway/commit/2fa070dd586700b236cbeed70491e556ac921156))


## v1.3.1 (2026-07-26)

### Bug Fixes

- Update Docker publish workflow to use JIFFY_REPO_PAT for authentication
  ([`d1e7e23`](https://github.com/javadib/jiffy_gateway/commit/d1e7e23aeaa05ce944a2d5a972ab1870a900c3c9))


## v1.3.0 (2026-07-26)

### Bug Fixes

- Add --skip-ci option to semantic-release command
  ([`79acecc`](https://github.com/javadib/jiffy_gateway/commit/79acecc96005baa98221c78c70c461f861422c43))

- Update release workflow to use JIFFY_REPO_PAT and revert version to 1.2.0
  ([`72f2903`](https://github.com/javadib/jiffy_gateway/commit/72f290347dc798c7007b89e60c8c2328e65b738e))

- Update sandbox image version and change semantic-release command to skip build
  ([`c9e0591`](https://github.com/javadib/jiffy_gateway/commit/c9e059189ab1bd92c9584d84723b8b104d6b206c))


## v1.2.0 (2026-07-21)

### Features

- Use serializer validation and store full payload in Redis
  ([`b4f3793`](https://github.com/javadib/jiffy_gateway/commit/b4f3793afe8c7a0e35cdefcf0d1958a2dc6dfa37))


## v1.0.1 (2026-07-20)

### Bug Fixes

- Resolve review feedback from @gemini-code-assist
  ([`976c02e`](https://github.com/javadib/jiffy_gateway/commit/976c02eb581ffa7769c563478925295183a5f522))


## v1.0.0 (2026-07-20)

- Initial Release

## v1.1.1 (2026-07-14)

### Bug Fixes

- All tests pass. Here's a summary of the fix: ‌
  ([`bc4ed39`](https://github.com/javadib/biznext_srv/commit/bc4ed392c8fc913975d072e9a924d8902c2c8147))

- All tests pass. Here's a summary of the fix: ‌
  ([`1156182`](https://github.com/javadib/biznext_srv/commit/1156182098232fe6ca703a47f223663f11301937))

- Review applied
  ([`1c64741`](https://github.com/javadib/biznext_srv/commit/1c647413a0322d09c09259f05c7ebd1f571a5ae2))


## v1.1.0 (2026-07-14)

### Bug Fixes

- Apply all review advises
  ([`080c0fb`](https://github.com/javadib/biznext_srv/commit/080c0fbc0c3a3cebb14103b81449d0130240aacd))

### Features

- **notification**: Implement notify package & notification app
  ([`fecbc5c`](https://github.com/javadib/biznext_srv/commit/fecbc5c9fd7792d3a9cf3fc561737191e7fbffad))

- **notification**: Implement notify package & notification app
  ([`4297075`](https://github.com/javadib/biznext_srv/commit/4297075269f02fd6a63fa059c05aa28c41bd3dd2))


## v1.0.0 (2026-07-13)

- Initial Release
