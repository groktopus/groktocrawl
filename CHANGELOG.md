# Changelog

All notable changes to GroktoCrawl are documented in this file.

## [0.14.0](https://github.com/groktopus/groktocrawl/compare/v0.13.0...v0.14.0) (2026-09-04)


### Features

* **parse:** add explicit hosted OCR mode ([ca59119](https://github.com/groktopus/groktocrawl/commit/ca591199cfd05144e45d2372afd659f2e86e6327))
* **parse:** add explicit hosted OCR mode ([81ab073](https://github.com/groktopus/groktocrawl/commit/81ab0731e75888d3a50a02e6c8a6e0a19f6b276b)), closes [#615](https://github.com/groktopus/groktocrawl/issues/615)
* **session:** opt in to independent steps ([83dd610](https://github.com/groktopus/groktocrawl/commit/83dd610967fb72423b85dfd70d0e7de1e912638a))


### Bug Fixes

* [#586](https://github.com/groktopus/groktocrawl/issues/586) barrier polish and test-fidelity hardening ([54487cf](https://github.com/groktopus/groktocrawl/commit/54487cfe453b23a2f40e2fae610640bcfd70b825))
* [#586](https://github.com/groktopus/groktocrawl/issues/586) barrier polish and test-fidelity hardening ([2308a30](https://github.com/groktopus/groktocrawl/commit/2308a306d068f85ad75142635d5c4e7d14cc0213))
* address review findings on [#586](https://github.com/groktopus/groktocrawl/issues/586) refusal semantics ([3323778](https://github.com/groktopus/groktocrawl/commit/3323778e9099dfd89488e960b4b0ae4ffb1cf970))
* **agent:** address PR [#597](https://github.com/groktopus/groktocrawl/issues/597) review findings on param-honoring fix ([7b79932](https://github.com/groktopus/groktocrawl/commit/7b7993231b58d06333c9f38d602a37ce9e0bb281))
* **agent:** honor crawl limit and agent max_credits request params ([482aa92](https://github.com/groktopus/groktocrawl/commit/482aa92873f0c5a5bd901837f1f30a9f93bf68b7))
* **agent:** make LLM call timeout configurable ([#589](https://github.com/groktopus/groktocrawl/issues/589)) ([33a4ea9](https://github.com/groktopus/groktocrawl/commit/33a4ea9d7ef26af1d6738f8e773b0719a6175a04))
* **browser-pool:** preserve lifecycle capacity during cleanup ([a9d890f](https://github.com/groktopus/groktocrawl/commit/a9d890ff9e98dbcb88cb49c3cc43d28b834e61cf))
* **ci:** give deleted-fork PRs the explicit fork summary in runtime-gate ([c1fbe4d](https://github.com/groktopus/groktocrawl/commit/c1fbe4dbd2a4ccfcf12cbaa1175622bbc952067c))
* **ci:** route null forks to fork-guard seam, exclude from self-hosted lane ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([8da8427](https://github.com/groktopus/groktocrawl/commit/8da8427ca5e05f473cd72edd4361ababfcee7cd0))
* **ci:** route null forks to the fork-guard seam, exclude from self-hosted lane ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([e34d998](https://github.com/groktopus/groktocrawl/commit/e34d9983be4316bbb69a6becd9d8aa1bd4fb5ecf))
* **cli:** omit crawl limit from payload when unset (PR [#597](https://github.com/groktopus/groktocrawl/issues/597) regression) ([35cba7b](https://github.com/groktopus/groktocrawl/commit/35cba7bba15b8e1aa00447dddc924a9910021819))
* **cli:** omit crawl limit from payload when unset (PR [#597](https://github.com/groktopus/groktocrawl/issues/597) regression) ([54b92b1](https://github.com/groktopus/groktocrawl/commit/54b92b11a8ca4ca8c122a1974c151c754d30edc7))
* complete vector search failure handling and regression coverage ([c7d1f84](https://github.com/groktopus/groktocrawl/commit/c7d1f84af0e23e517ee083cc45424f80246c064e))
* **compose:** pass Qdrant timeout tuning through to semantic-svc ([#588](https://github.com/groktopus/groktocrawl/issues/588)) ([4d94e99](https://github.com/groktopus/groktocrawl/commit/4d94e99ac14f385d3f8b346df4373c0a7c1cadf3))
* **compose:** pass Qdrant timeout tuning through to semantic-svc ([#588](https://github.com/groktopus/groktocrawl/issues/588)) ([9ba8c66](https://github.com/groktopus/groktocrawl/commit/9ba8c66e4d47eb4e213a2ac5023349c6779e73ae))
* detect Fastly JS-challenge pages and refuse barrier content ([#586](https://github.com/groktopus/groktocrawl/issues/586)) ([8d7dc82](https://github.com/groktopus/groktocrawl/commit/8d7dc829ee34be6b5ae09fbb4270ab9c930dc097))
* detect Fastly JS-challenge pages and refuse barrier content at all consumers ([#586](https://github.com/groktopus/groktocrawl/issues/586)) ([4b66e0a](https://github.com/groktopus/groktocrawl/commit/4b66e0a0dc41d9f511837433f5d8b625672fffe9))
* fence concurrent session steps ([0b005a9](https://github.com/groktopus/groktocrawl/commit/0b005a96a52e2630e75a602225dc6c4fb72ea7ba))
* handle vector search failures in HTTP and SSE responses ([34b4975](https://github.com/groktopus/groktocrawl/commit/34b4975bc7baaf25510ed34029b957f95b59de70))
* honor crawl limit and agent max_credits request params ([30ee96b](https://github.com/groktopus/groktocrawl/commit/30ee96bdd38e199444165271954059b0530c34f8))
* make LLM call timeout configurable ([9947d32](https://github.com/groktopus/groktocrawl/commit/9947d328a4238bfebbfb30703875e503605bdb6a))
* **parse:** call PDF parser with OCR option ([8e6f4ec](https://github.com/groktopus/groktocrawl/commit/8e6f4ec0f254bf1afb0f4f7987005b60bbf330d2))
* preserve semantic vector search failures ([8ce40ec](https://github.com/groktopus/groktocrawl/commit/8ce40ec827308c75927e0ba6d61096d0435666f9))
* preserve Tier 3 barrier provenance in default-flow terminal dict ([18ecaff](https://github.com/groktopus/groktocrawl/commit/18ecaff86f4d9fadcc0eb4a3ec21ff3258f2f22c)), closes [#586](https://github.com/groktopus/groktocrawl/issues/586)
* preserve Tier 3 barrier provenance in default-flow terminal dict ([#586](https://github.com/groktopus/groktocrawl/issues/586)) ([59b2084](https://github.com/groktopus/groktocrawl/commit/59b208448b9f0efe974e517df6ec7229cced4fd2))
* propagate vector-search failures in find-similar ([#588](https://github.com/groktopus/groktocrawl/issues/588)) ([38b097a](https://github.com/groktopus/groktocrawl/commit/38b097a44c1b7e13b6b9ac74b598a194b0f1b520))
* propagate vector-search failures in find-similar ([#588](https://github.com/groktopus/groktocrawl/issues/588)) ([74b759b](https://github.com/groktopus/groktocrawl/commit/74b759ba7810b27c119c7fdc6401d34bc8226d61))
* **research:** drain and preserve bounded scrape batches ([918ad4c](https://github.com/groktopus/groktocrawl/commit/918ad4c92ed2e045772fbcc5c7d367ea1ba9ec69))
* **research:** retain URL parameters and prioritize novel gap evidence ([394a0f7](https://github.com/groktopus/groktocrawl/commit/394a0f762043cf2960eeae3513151b3d1033b96a))
* **scraper:** drain all retired processes when reaper is cancelled ([e38b825](https://github.com/groktopus/groktocrawl/commit/e38b825e796e1344a5632aebe86c223c674a1d3f))
* **scraper:** drop duplicated proxy-logging comment from polish diff ([8c791b6](https://github.com/groktopus/groktocrawl/commit/8c791b60187106fbb7b934e98008b0a9c71385c4))
* **scraper:** gate recovery on meaningful gain; strip head chrome ([#587](https://github.com/groktopus/groktocrawl/issues/587)) ([082d0ce](https://github.com/groktopus/groktocrawl/commit/082d0ce4a1ebcc507ad625785e5031e46457765f))
* **scraper:** migrate legacy string source_html_size on cache reads ([b544aef](https://github.com/groktopus/groktocrawl/commit/b544aefe198550781472c9f5e90fd6228706be99))
* **scraper:** recover full body on low-yield card-grid extractions ([#587](https://github.com/groktopus/groktocrawl/issues/587)) ([7ab1b4d](https://github.com/groktopus/groktocrawl/commit/7ab1b4d02f277a5fdf19f0359c89188923cc8dd2))
* **scraper:** recover full body on low-yield card-grid extractions ([#587](https://github.com/groktopus/groktocrawl/issues/587)) ([0e39754](https://github.com/groktopus/groktocrawl/commit/0e397541bb751831ba9503a959733b6d8e4dd63b))
* **search:** preserve URL identity and progressive bounded enrichment ([40d89e5](https://github.com/groktopus/groktocrawl/commit/40d89e5e3f558485e0beded163689c218d39fe67))
* **semantic:** ceil QDRANT_CLIENT_TIMEOUT so it never lands below the wrapper ([8c0fcff](https://github.com/groktopus/groktocrawl/commit/8c0fcffa1891a9dfeaff67b31dc57fd33ee3e8a9))
* **session:** bound and drain storage work with atomic append logs ([7513c78](https://github.com/groktopus/groktocrawl/commit/7513c780b76fdb7e2a5b7566a8641049ff1d2946))
* **session:** drain heartbeat cleanup and document concurrent-step timings ([f13c70e](https://github.com/groktopus/groktocrawl/commit/f13c70e2c5f4f12c1d019d8a6c514036f7672aca))
* **youtube:** pace transcript acquisition ([86a1946](https://github.com/groktopus/groktocrawl/commit/86a19468754e9caa2f4e3389c5e2d15167b689d5))
* **youtube:** pace transcript acquisition ([567f1a1](https://github.com/groktopus/groktocrawl/commit/567f1a154b73ef324fe8d379f58e2fe00717ce6e))


### Performance Improvements

* bound semantic model inference ([1265a62](https://github.com/groktopus/groktocrawl/commit/1265a627e5394c1ff9f313f8f14e9b01d6aebd32))
* bound semantic model inference ([4e001df](https://github.com/groktopus/groktocrawl/commit/4e001df84ee7c3b8768ab3ec62ed2b1304a4a2f1))
* **deploy:** support bounded scraper scale-out and shared origin pacing ([0c311ae](https://github.com/groktopus/groktocrawl/commit/0c311aec6f950b9f352bdbc9e97b242ad837aab5))
* **research:** acquire sources as queries complete ([57d4634](https://github.com/groktopus/groktocrawl/commit/57d4634899d79a7d992d6ea870a286a195ff4608))
* **research:** complete cross-pass source reuse ([6f1b59e](https://github.com/groktopus/groktocrawl/commit/6f1b59ed77fa12d506e77cbf9b6efc6b439f6fdb))
* **research:** pipeline discovery, reuse sources, and synthesize once ([c99d36a](https://github.com/groktopus/groktocrawl/commit/c99d36af01b8ea2cf3ee7ca38bb0beb041a15720))
* **research:** reuse sources across discovery passes ([5c2d3fc](https://github.com/groktopus/groktocrawl/commit/5c2d3fc12ebe7dce68b3677978fc35436663ab53))
* **research:** synthesize one final answer after evidence coverage ([d76c41b](https://github.com/groktopus/groktocrawl/commit/d76c41b310e7d3e06a057b98cb3d155bd775f122))
* **scraper:** reuse bounded browser processes ([83d95d8](https://github.com/groktopus/groktocrawl/commit/83d95d8dcf03d4a2134a9f4f965eeedb01b7bcb6))
* **scraper:** reuse browser processes and support bounded scale-out ([5366343](https://github.com/groktopus/groktocrawl/commit/53663433044f646c5ea60415691af40e3278680c))
* **search:** reuse bounded source acquisition across stages ([00e73f6](https://github.com/groktopus/groktocrawl/commit/00e73f6ed99656577101741ebdb10769b318b64c))
* **semantic:** bound native inference and keep it off the event loop ([cfb65db](https://github.com/groktopus/groktocrawl/commit/cfb65db195a67cd4434b416202f8e5d9b1cc132e))
* **session:** allow bounded independent steps with atomic persistence ([3afcfca](https://github.com/groktopus/groktocrawl/commit/3afcfcacea2179c9d3eeab5a1985e3f8f457a6c2))
* **session:** offload and batch persistence ([adb39ea](https://github.com/groktopus/groktocrawl/commit/adb39ea19609982420c81b5c453eaf02cdce394e))


### Documentation

* list LLM_CALL_TIMEOUT in public surface inventory ([7c9b210](https://github.com/groktopus/groktocrawl/commit/7c9b21005849dea785fe839c958059ce24bdaaea))
* **perf:** record topology evidence and configuration inventory ([653b8d2](https://github.com/groktopus/groktocrawl/commit/653b8d2738387f897c7d9ceb92144b18d5d2ef3a))
* record 2026-08-22 branch-protection bypass in audit runbook ([#583](https://github.com/groktopus/groktocrawl/issues/583)) ([c7bac21](https://github.com/groktopus/groktocrawl/commit/c7bac21bcd7303ef6d09fdb6afd19646de582ea4))
* record 2026-08-22 branch-protection bypass in audit runbook ([#583](https://github.com/groktopus/groktocrawl/issues/583)) ([2ea25e7](https://github.com/groktopus/groktocrawl/commit/2ea25e797d1eb8398168c9ba6ebc70759f2ea14b))
* reflect Round 2 barrier, search, and fork-protection changes ([eb3a06a](https://github.com/groktopus/groktocrawl/commit/eb3a06acf6f3d0f7f1157887ed0816b1c519e97e))
* reflect Round 2 barrier, search, and fork-protection changes ([da0d4b6](https://github.com/groktopus/groktocrawl/commit/da0d4b63b44f0fa53f61c6bcfcde7ea871b6cee2))
* update README for configurable timeouts and error-propagation ([2b3738f](https://github.com/groktopus/groktocrawl/commit/2b3738f2c941fbd535eb16ddcb15cb16a8b5f1d0))
* update README for configurable timeouts and error-propagation changes ([a9bcccb](https://github.com/groktopus/groktocrawl/commit/a9bcccb36f4837fea75a326d5b87d098672617cb))


### CI/CD

* address review pass on fork-PR guard and --verify-rulesets ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([726858c](https://github.com/groktopus/groktocrawl/commit/726858cb3328bb90068a39331bf2dffa1f4e0c54))
* align integration AnyDoc version ([1a47074](https://github.com/groktopus/groktocrawl/commit/1a4707461b5ec9b938b0e06554474227381623b6))
* allocate integration-lane host ports dynamically at run time ([d3a4a09](https://github.com/groktopus/groktocrawl/commit/d3a4a097a0bb2d4c53941c679207dd91d76a124d))
* deconflict integration-lane host ports from co-tenant stacks ([1b26623](https://github.com/groktopus/groktocrawl/commit/1b266238ad141a6d36358fe127422544ea0beef1))
* fix python-source interpolation of allocated host ports ([5576b59](https://github.com/groktopus/groktocrawl/commit/5576b593d9237c51231b3d474b7337953e731baa))
* harden fork-PR protection on self-hosted lane ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([bb68d9e](https://github.com/groktopus/groktocrawl/commit/bb68d9e576abf410d73f716f40d5d82af075522f))
* harden fork-PR protection on the self-hosted runner lane ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([7380bbf](https://github.com/groktopus/groktocrawl/commit/7380bbfb5628236717281141981f2f769482ccca))
* **session:** use the declared service test dependency group ([a468bf0](https://github.com/groktopus/groktocrawl/commit/a468bf00cb22be20223c2d1d5a5bd273b613b665))
* string-render droid-review fork gate; harden gha expr sim ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([85c28ee](https://github.com/groktopus/groktocrawl/commit/85c28ee1b04ac3b0b3afd4726f41a185138b8741))
* string-render the droid-review fork gate; harden gha expr sim ([#562](https://github.com/groktopus/groktocrawl/issues/562)) ([552a1ec](https://github.com/groktopus/groktocrawl/commit/552a1ec3a265ec1b6fd6f87b199839535d0626c0))


### Tests

* add hermetic Gutenberg adapter coverage and drop stale live-test duplicate ([#581](https://github.com/groktopus/groktocrawl/issues/581)) ([90e2f8c](https://github.com/groktopus/groktocrawl/commit/90e2f8c0ca8584ce3b9ae8e4be35f024539fa479))
* address droid-review P2 fidelity gaps in gutenberg twin ([1396c82](https://github.com/groktopus/groktocrawl/commit/1396c828045ff0a8372dc77f73bcf9f45989fe42))
* address droid-review round-5 findings (uptime decoupling, fake-client isolation) ([deac1be](https://github.com/groktopus/groktocrawl/commit/deac1be322274536c97cf6797043f81c40e64f76))
* address review — restore seam 404 fallback, order-safe metric assertions ([8bd6bc5](https://github.com/groktopus/groktocrawl/commit/8bd6bc559e63e9493d4ef3f13799413047c3c820))
* **agent:** address review findings on promoted LLM tests ([5fd51a0](https://github.com/groktopus/groktocrawl/commit/5fd51a0229b635f20dbd777f001fd052fac8ee9e))
* **agent:** promote timeout/find-similar edge-case coverage ([f7aa6ee](https://github.com/groktopus/groktocrawl/commit/f7aa6eec77935c18bb9412bd8779ab3f613f5ee6))
* **agent:** promote timeout/find-similar edge-case coverage ([80e31be](https://github.com/groktopus/groktocrawl/commit/80e31bec91e40610dc51f4a94ab82f6ba0fef5ad))
* close remaining droid-review fidelity gaps in gutenberg twin ([1a281a0](https://github.com/groktopus/groktocrawl/commit/1a281a0b8cb9bda05b69d18afbe7a1f4c4c5d1ac))
* **compose:** govern .env.sample skips for the containerized integration lane ([d8b3e80](https://github.com/groktopus/groktocrawl/commit/d8b3e8064d08c6fafcfac2459306e0b9e1e53352))
* drive gutendex override through the production fetch path ([a00adbf](https://github.com/groktopus/groktocrawl/commit/a00adbfffb9549984b1cb0cd852fee610d95a43b))
* govern curl_cffi skips for the integration lane ([#586](https://github.com/groktopus/groktocrawl/issues/586) polish) ([d0c181e](https://github.com/groktopus/groktocrawl/commit/d0c181ef428e977edfcd1cd7fcde857b04cd5bcc))
* harden hermetic Gutenberg suite (containment helper, seam guards, fixture teeth) ([26cf2c1](https://github.com/groktopus/groktocrawl/commit/26cf2c1a2a388b5184b636e38e5c323736b67fb1))
* harden hermetic Gutenberg suite (containment helper, seam guards, fixture teeth) ([82f0cbd](https://github.com/groktopus/groktocrawl/commit/82f0cbd8f8594f8178640a933a40e103f0bbccea))
* hermetic Gutenberg adapter coverage ([6e71060](https://github.com/groktopus/groktocrawl/commit/6e710606aff24690d9bcadab7850fd51e64388aa))
* hermetic gutenberg twin for the integration lane ([6ef80ab](https://github.com/groktopus/groktocrawl/commit/6ef80abd2f7e327b41778f5e09df831a4dfc9121))
* hermetic gutenberg twin for the integration lane ([7d97c6e](https://github.com/groktopus/groktocrawl/commit/7d97c6eb42b2cb931d1680f3b95ed6db0bf1a918)), closes [#581](https://github.com/groktopus/groktocrawl/issues/581)
* isolate YouTube pacing fixture to remove order dependence ([87c245a](https://github.com/groktopus/groktocrawl/commit/87c245affa62219ed53d302552f23c37984f1eb0))
* isolate YouTube pacing fixture to remove order dependence ([6e8efe1](https://github.com/groktopus/groktocrawl/commit/6e8efe1459bfce8efb85bbda324d1664a8eb4266))
* isolate YouTube pacing fixture to remove order dependence ([177f3d3](https://github.com/groktopus/groktocrawl/commit/177f3d35e9a8c5a893784a04cdc99769448a69f0))
* move slopsearx digest-pin contract tests to the new image digest ([3ab2211](https://github.com/groktopus/groktocrawl/commit/3ab2211833fa706ad019edc4c46ae2c7b8edbcd0))
* rebuild default-flow barrier twins around the real envelope shape ([d67b690](https://github.com/groktopus/groktocrawl/commit/d67b6909b8230ed9d1312f87d115e96e89c69424)), closes [#586](https://github.com/groktopus/groktocrawl/issues/586)
* **research:** rely on behavioral credit accounting coverage ([c6a6dbc](https://github.com/groktopus/groktocrawl/commit/c6a6dbc85fe2bae25c343e1913053e52b580a0b6))
* **scaleout:** declare Valkey environment requirement for governed skip ([e263007](https://github.com/groktopus/groktocrawl/commit/e263007d7d089c23230e131e99142e25fb2a31a0))
* **scaleout:** exercise Valkey reservations and gateway drain ([c390500](https://github.com/groktopus/groktocrawl/commit/c390500fc02d14f1a11e7d817244106f1ccb5f4c))
* **scraper:** harden yield-recovery follow-ups from [#587](https://github.com/groktopus/groktocrawl/issues/587) review ([8ca9509](https://github.com/groktopus/groktocrawl/commit/8ca95091391ce8f9b29c0f85393a98f56c598351))
* **scraper:** harden yield-recovery follow-ups from [#587](https://github.com/groktopus/groktocrawl/issues/587) review ([7adc703](https://github.com/groktopus/groktocrawl/commit/7adc7031f47ae3ac44892487ff101629fbf1fcd4))


### Chores

* allow scraper as runtime-path import in agent-svc deptry config ([80b8171](https://github.com/groktopus/groktocrawl/commit/80b81715191dd52f1827673e7a25fb1c30f7a64f))
* apply misc polish follow-ups from misc-qa-hardening scrutiny ([3ac8334](https://github.com/groktopus/groktocrawl/commit/3ac8334b0406e43113638b74cd554b4a4c0e2c0e))
* apply misc polish follow-ups from misc-qa-hardening scrutiny ([7584c8c](https://github.com/groktopus/groktocrawl/commit/7584c8c80a0d3e763638d5fa372bc25ae3d96898))
* bump slopsearx image pins to DDG-resilient build ([#413](https://github.com/groktopus/groktocrawl/issues/413)) ([029551f](https://github.com/groktopus/groktocrawl/commit/029551ff541b34c373974485f8e0e8061c7dd9f5))
* bump slopsearx image pins to DDG-resilient build ([#413](https://github.com/groktopus/groktocrawl/issues/413)) ([6a392f4](https://github.com/groktopus/groktocrawl/commit/6a392f4f9b72f33b480687c0132f51064cb3f33b))

## [0.13.0](https://github.com/groktopus/groktocrawl/compare/v0.12.1...v0.13.0) (2026-08-22)


### Features

* add autonomous CAPTCHA detection and recovery ([#456](https://github.com/groktopus/groktocrawl/issues/456)) ([c77266b](https://github.com/groktopus/groktocrawl/commit/c77266b9e411e9b75351305b3607252b32569fb7)), closes [#455](https://github.com/groktopus/groktocrawl/issues/455)
* add deterministic LLM contract scenarios ([f71781b](https://github.com/groktopus/groktocrawl/commit/f71781bd3ac1af03125cd14624c1e6102e2d3726))
* add deterministic LLM contract scenarios ([86dc5f4](https://github.com/groktopus/groktocrawl/commit/86dc5f4cf20b164694310d457e1f7fb7b7245cd3))
* add deterministic SlopSearX contract twin ([216c65f](https://github.com/groktopus/groktocrawl/commit/216c65f416fa4c667c7f1cf4842e8af90c71d915))
* add deterministic SlopSearX contract twin ([1445642](https://github.com/groktopus/groktocrawl/commit/144564273be86ac137304c3892afadbf15dca983))
* **agent,cli:** retryable rate-limit contract (ADR-0053) ([2eab2b7](https://github.com/groktopus/groktocrawl/commit/2eab2b749bdcef5c42d3ea042a93b48b95719e69))
* **agent,cli:** retryable rate-limit contract (ADR-0053) ([eba5122](https://github.com/groktopus/groktocrawl/commit/eba5122efd8f279ef3a8ac087f43f286cb00a9e1))
* **agent:** add --search-type flag for research depth control ([f7dd947](https://github.com/groktopus/groktocrawl/commit/f7dd94709b5c1b2860e396734045a6d73896863e))
* **agent:** add --search-type flag for research depth control ([f7dd947](https://github.com/groktopus/groktocrawl/commit/f7dd94709b5c1b2860e396734045a6d73896863e))
* **agent:** add portable research state projection ([#464](https://github.com/groktopus/groktocrawl/issues/464)) ([2b40ad0](https://github.com/groktopus/groktocrawl/commit/2b40ad0122e2ef43a2da23c5f132940613ec93bc))
* **agent:** harden research-memory cache compatibility and freshness ([fa2e14f](https://github.com/groktopus/groktocrawl/commit/fa2e14f22693a755b5bbff52ed9f3105d5cec412)), closes [#529](https://github.com/groktopus/groktocrawl/issues/529)
* allow maintainer to merge own PRs (review bypass only) ([1ec81ba](https://github.com/groktopus/groktocrawl/commit/1ec81baba000719e9b1eb1059a6d473dc6634d59))
* allow maintainer to merge own PRs (review bypass only) ([b17bf2f](https://github.com/groktopus/groktocrawl/commit/b17bf2f648da1bf760740ed569ffd42f6fd3f64b)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* allow maintainer to merge own PRs (review bypass only) ([#518](https://github.com/groktopus/groktocrawl/issues/518)) ([1ec81ba](https://github.com/groktopus/groktocrawl/commit/1ec81baba000719e9b1eb1059a6d473dc6634d59))
* **ashby:** add official Ashby API tiers to the adapter ([bb66bbf](https://github.com/groktopus/groktocrawl/commit/bb66bbf2137d1b2411f2af32f9864e324a6df97d))
* **ashby:** add official Ashby API tiers to the AshbyHQ adapter ([544bb7e](https://github.com/groktopus/groktocrawl/commit/544bb7eb447894ab19f3fd7259c12e283ae7dc83))
* **compose:** expose direct SlopSearX MCP service ([629e54e](https://github.com/groktopus/groktocrawl/commit/629e54eb1a4ab412a26f38d89eae77ebfb52f005))
* **compose:** expose SlopSearX MCP service ([f406ff6](https://github.com/groktopus/groktocrawl/commit/f406ff6d08a74920b8c66d528991f727670a5895))
* **mcp-svc:** complete MCP tool surface and fix adapter correctness ([b4b7982](https://github.com/groktopus/groktocrawl/commit/b4b79822ac901d2a6bf198b48cf355c38ec5d786))
* **mcp-svc:** complete MCP tool surface and fix adapter correctness ([58802c1](https://github.com/groktopus/groktocrawl/commit/58802c1d9bf430ae5ff255e6a6f8a7a05c1180ea))
* **metrics:** add stage timer, gauge inc/dec, and shared stage helpers ([52ef618](https://github.com/groktopus/groktocrawl/commit/52ef618d2c09a53ccae80cf48b0d6ba9ecbc9147))
* **parse-svc:** adopt firecrawl-anydoc as the conversion engine ([3721a05](https://github.com/groktopus/groktocrawl/commit/3721a059dfc43b1c3a05e57c0e1cf00f7609740b))
* **parse-svc:** adopt firecrawl-anydoc as the conversion engine ([bb96263](https://github.com/groktopus/groktocrawl/commit/bb96263083b7a6a8b75b5e7187b563d773054fe8)), closes [#523](https://github.com/groktopus/groktocrawl/issues/523)


### Bug Fixes

* add graceful shutdown with drain timeout to MCP server ([#417](https://github.com/groktopus/groktocrawl/issues/417)) ([3541515](https://github.com/groktopus/groktocrawl/commit/354151517709263fd68c6d5711d8b48cadcee380)), closes [#410](https://github.com/groktopus/groktocrawl/issues/410)
* add transient Playwright error recovery ([f6f6c71](https://github.com/groktopus/groktocrawl/commit/f6f6c71c3d96e9d84d6869962894145a482922f7))
* add transient Playwright error recovery ([#412](https://github.com/groktopus/groktocrawl/issues/412)) ([b69efba](https://github.com/groktopus/groktocrawl/commit/b69efba696e652ff2f279e1f6c4ca3db51fb30ea))
* **admission:** refund granted waiter weight on cancellation ([e9573ed](https://github.com/groktopus/groktocrawl/commit/e9573edc27bc715b5c48d5dc7fa1d2140b45bed6))
* **agent,cli:** address review findings on retry metadata safety ([66c2cdc](https://github.com/groktopus/groktocrawl/commit/66c2cdc068e9c311aec9231e3083737283098f8f))
* **agent:** align polling research with streaming ([#463](https://github.com/groktopus/groktocrawl/issues/463)) ([4fe03f6](https://github.com/groktopus/groktocrawl/commit/4fe03f61b9ebb87b0a6d4fb2c5fddc6dd41b5caf))
* **agent:** cap answer sources at num_sources after URL dedup ([5bf9b71](https://github.com/groktopus/groktocrawl/commit/5bf9b7147e40e51736f0c362fc74c7adac2e7948))
* **agent:** correct cancelled/completed telemetry across agent paths ([c3a31a3](https://github.com/groktopus/groktocrawl/commit/c3a31a38e4cdf7f7044d5c77ded787d210c8f8e9))
* **agent:** degrade find_similar on any semantic backend failure, not just 503 ([#545](https://github.com/groktopus/groktocrawl/issues/545)) ([0cfbd1d](https://github.com/groktopus/groktocrawl/commit/0cfbd1d1b114666b912eeee1cbc9dc1d993e9619))
* **agent:** harden hybrid blend dedup, malformed URLs, and diversity floor ([81f2104](https://github.com/groktopus/groktocrawl/commit/81f2104633a7b27e8d437c7a6c9d50b62498a8a8))
* **agent:** opt-in downstream 429 classification for retry paths ([6554ea1](https://github.com/groktopus/groktocrawl/commit/6554ea1719a9ec9bfde10fad6f5e3ec0f0129dcd))
* **agent:** preserve compact sources and single-flight SWR refresh ([802be94](https://github.com/groktopus/groktocrawl/commit/802be945ecc8e0365ceb3be860c259b54ea29b5f))
* **agent:** record research memory Valkey lookup errors ([1db27b4](https://github.com/groktopus/groktocrawl/commit/1db27b44a7abdfa3b472509a4f884aeb53e63124))
* **agent:** track stream-mode crawl workload telemetry ([ca74265](https://github.com/groktopus/groktocrawl/commit/ca74265a31520e91bb4462690fcb432cad993eb3))
* **agent:** treat degraded lightweight scrape as insufficient ([27600cf](https://github.com/groktopus/groktocrawl/commit/27600cf62c8f4c28ba2a95ae8e50ed9e926b7dda))
* **agent:** wire search_type through streaming pipeline and SSE plan event ([5345c7e](https://github.com/groktopus/groktocrawl/commit/5345c7e1e652250f0bfa25d14082dbf06f40af62))
* **agent:** wire search_type through streaming pipeline and SSE plan event ([5345c7e](https://github.com/groktopus/groktocrawl/commit/5345c7e1e652250f0bfa25d14082dbf06f40af62))
* **agent:** wire search_type through streaming pipeline and SSE plan event ([42e8a5c](https://github.com/groktopus/groktocrawl/commit/42e8a5cba6beec5a6e6c9381e3cb21b61278ded7))
* align tests with explicit LLM model contract ([cc1bc41](https://github.com/groktopus/groktocrawl/commit/cc1bc41a362fac2547e80e7f2ebed8c6f71d967e))
* align tests with explicit LLM model contract ([ba775de](https://github.com/groktopus/groktocrawl/commit/ba775de65a88401c9e6cdb5a545c8822d75f4d2c))
* **ashby:** gate RPC tier to individual postings and document the key ([277e6b8](https://github.com/groktopus/groktocrawl/commit/277e6b8d4636b9bdc41fc14173b64f567a5a240c))
* **ashbyhq:** escape pipes and newlines in API markdown table cells ([95d1c76](https://github.com/groktopus/groktocrawl/commit/95d1c76da503401c2a7855c7517555b64b1ff51b))
* **ashbyhq:** escape pipes and newlines in markdown table cells ([a613e32](https://github.com/groktopus/groktocrawl/commit/a613e322c8b722a54451e4bb9b77770ef526394f))
* **benchmarks:** fail cleanly when StackRunner is unwired ([2138462](https://github.com/groktopus/groktocrawl/commit/2138462fd99dc5baa4af1f334d7f2ad304b5e4ec))
* **benchmarks:** reject non-positive --runs with a clean error ([accf580](https://github.com/groktopus/groktocrawl/commit/accf5807fd0ac4f80f4c9ceabca1019a06f43ae3))
* **browser:** do not count failed session creation as destroyed ([ea98f96](https://github.com/groktopus/groktocrawl/commit/ea98f969eb264dcdb16f9561540c9d4423ae69ca))
* **browser:** stop manually started Playwright controllers ([c3e99a8](https://github.com/groktopus/groktocrawl/commit/c3e99a8fd3779db864e1ff8571aa301dff7b3bd2))
* **browser:** stop manually started Playwright controllers ([6cfd068](https://github.com/groktopus/groktocrawl/commit/6cfd06889487df5956eab0dd2e2e5c575c4880b1))
* **ci:** classify root *.md as docs-only and contract-test RUNTIME_SERVICES ([7a942fd](https://github.com/groktopus/groktocrawl/commit/7a942fdc6b8c6d68e09f9928fc701f3d46c53843))
* **ci:** classify root *.md as docs-only and contract-test RUNTIME_SERVICES ([3153b8b](https://github.com/groktopus/groktocrawl/commit/3153b8b29c6a19d9819fdb63a5cdf5c4b9bab127))
* **ci:** copy benchmarks into integration container ([c0b703a](https://github.com/groktopus/groktocrawl/commit/c0b703abcb7802053ea9981724ce81b56d6614c7))
* **ci:** make mcp-svc test steps pass under CI config ([b45ed7c](https://github.com/groktopus/groktocrawl/commit/b45ed7cefcbbf5dcc33b1ee99bb5e0b1cc85500d))
* **ci:** make release-please version tracking explicit ([29199b2](https://github.com/groktopus/groktocrawl/commit/29199b2a2702529ac11e6eb75d21b338df0d05ff))
* **ci:** make release-please version tracking explicit ([0f1902d](https://github.com/groktopus/groktocrawl/commit/0f1902d6b75d250aba71bfaf6bfa2630ac730603))
* **ci:** treat .github/ markdown as docs-only per review ([45b32d4](https://github.com/groktopus/groktocrawl/commit/45b32d4666c10ff7429976cfaba199603a909c9f))
* complete LLM scenario error contracts ([b62345e](https://github.com/groktopus/groktocrawl/commit/b62345efd56e939d4b4ce128709acca830d8c4f2))
* **compose:** bound browser workloads and profile indexing ([#482](https://github.com/groktopus/groktocrawl/issues/482)) ([47967cd](https://github.com/groktopus/groktocrawl/commit/47967cd4d67a6c94d6d5d37d7027a1a662ee89b8))
* **compose:** delegate slopsearx-mcp grant defaults to upstream secure policy ([#579](https://github.com/groktopus/groktocrawl/issues/579)) ([3df5948](https://github.com/groktopus/groktocrawl/commit/3df5948e70374443fcc4c8c32a05445ff85a42e5)), closes [#578](https://github.com/groktopus/groktocrawl/issues/578)
* **compose:** gate slopsearx-mcp behind opt-in mcp profile ([#558](https://github.com/groktopus/groktocrawl/issues/558)) ([60fab8d](https://github.com/groktopus/groktocrawl/commit/60fab8d601109b823a0ffd6f69557fc69835f809))
* **compose:** stop slopsearx-mcp aborting a no-config docker compose up ([8e53441](https://github.com/groktopus/groktocrawl/commit/8e53441950fb07b0768d1da19db9b0b1a57665a6))
* differentiate Playwright browser errors from generic upstream errors ([#416](https://github.com/groktopus/groktocrawl/issues/416)) ([85dc819](https://github.com/groktopus/groktocrawl/commit/85dc8199275062827ab94a0cd7dc96acedade8e0)), closes [#408](https://github.com/groktopus/groktocrawl/issues/408)
* document BRAVE_API_KEY and surface search degradation warnings ([#414](https://github.com/groktopus/groktocrawl/issues/414)) ([a9e33c4](https://github.com/groktopus/groktocrawl/commit/a9e33c414e80b8daede21a9f41c0c8cb97223c3b)), closes [#406](https://github.com/groktopus/groktocrawl/issues/406)
* drop release-please bypass exemption (dependabot-only review policy) ([01d2f9f](https://github.com/groktopus/groktocrawl/commit/01d2f9f5fed93e34b821d2cc11e2993dfbf39083))
* drop release-please bypass exemption (dependabot-only review policy) ([c42748f](https://github.com/groktopus/groktocrawl/commit/c42748f3dc132fb7a2b795c253586f1e0e4b246e)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* emit parseable semantic service metrics ([#451](https://github.com/groktopus/groktocrawl/issues/451)) ([1569fca](https://github.com/groktopus/groktocrawl/commit/1569fca06841314bfd19a8104100f4a90f726694))
* harden research-memory cache compatibility and freshness ([d1d2e65](https://github.com/groktopus/groktocrawl/commit/d1d2e650c03c4d69d8e1e110e2ad72c9ee3e1a6e))
* isolate search twin integration lane ([d57d069](https://github.com/groktopus/groktocrawl/commit/d57d06907420d7b91b1e838b417824c78dd29f1d))
* make LLM fixture deterministic with sources ([415735e](https://github.com/groktopus/groktocrawl/commit/415735eccc66433efe415d08cf4d93dc001d026d))
* **mcp-svc:** address review findings on monitor contract, SSRF, retries ([f29f4e8](https://github.com/groktopus/groktocrawl/commit/f29f4e8cda9c4c61759f80368ebd227057abfbf8))
* **mcp-svc:** fail-closed DNS-rebinding default, address review findings ([3bf116e](https://github.com/groktopus/groktocrawl/commit/3bf116e9f4ce63aa2762061ec6520c891dbe994f))
* **mcp-svc:** make DNS-rebinding Host allowlist explicit and configurable ([ffdc57c](https://github.com/groktopus/groktocrawl/commit/ffdc57c2542052752fcf69f406f2612ab9626ace))
* **mcp-svc:** make DNS-rebinding Host allowlist explicit and configurable ([4d76637](https://github.com/groktopus/groktocrawl/commit/4d7663797cdb2f71d411a861f73b97e55044d1f7)), closes [#524](https://github.com/groktopus/groktocrawl/issues/524)
* **mcp-svc:** wire MCP_ALLOWED_ORIGINS through docker-compose ([6971bea](https://github.com/groktopus/groktocrawl/commit/6971bea66d54e839b1487ac622327b59e26413f9))
* **metrics:** distinguish cache lookup errors from misses ([7c69e21](https://github.com/groktopus/groktocrawl/commit/7c69e213dcc97ef69f2301f53254ab8349bb6db6))
* **metrics:** omit invalid OpenMetrics timestamps ([5371956](https://github.com/groktopus/groktocrawl/commit/53719569dfba64c7dac0019e0727ceaaf083e390))
* **metrics:** omit OpenMetrics timestamps ([#488](https://github.com/groktopus/groktocrawl/issues/488)) ([e1ac66d](https://github.com/groktopus/groktocrawl/commit/e1ac66de56b9f8705cef58f4362aee9a5c744e97))
* **metrics:** record search-stream TTFB on empty results ([2a88e29](https://github.com/groktopus/groktocrawl/commit/2a88e295b0cfd778546ffab0a2a1d531ca355ba8))
* **metrics:** record stream TTFB at first real event ([f8c2608](https://github.com/groktopus/groktocrawl/commit/f8c260870430125b43073cc2cb3879751086b20f))
* omit integration_id from Ruleset B required status checks payload ([8fe4bea](https://github.com/groktopus/groktocrawl/commit/8fe4bea1fc1be6df6410a0e36dc1ee11197a3e5a))
* omit integration_id from Ruleset B required status checks payload ([865a41a](https://github.com/groktopus/groktocrawl/commit/865a41ad96a8bdc8bdd346b3f19e54b1b483710c)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* omit integration_id from Ruleset B required status checks payload ([#515](https://github.com/groktopus/groktocrawl/issues/515)) ([8fe4bea](https://github.com/groktopus/groktocrawl/commit/8fe4bea1fc1be6df6410a0e36dc1ee11197a3e5a))
* **politeness:** read-only per-tier checks and aborted-delay rollback ([911e90d](https://github.com/groktopus/groktocrawl/commit/911e90db04f21d8cf759c1ebc62463981a557acb))
* reject unsupported version inferences in research ([10f0c8c](https://github.com/groktopus/groktocrawl/commit/10f0c8c300cade6d6de63001459469ee3ac6f976))
* reject unsupported version inferences in research ([5cd4a0a](https://github.com/groktopus/groktocrawl/commit/5cd4a0acfdff638011f1de057620464ad713c6a2))
* remove hardcoded gpt-4o-mini defaults from research modules ([#415](https://github.com/groktopus/groktocrawl/issues/415)) ([0dc1b1e](https://github.com/groktopus/groktocrawl/commit/0dc1b1eedc42f19ef65df43eafe0fa3a89613b35))
* satisfy service dependency and fixture contracts ([171c4d9](https://github.com/groktopus/groktocrawl/commit/171c4d9a9c44866102751ac0d262d0d9f57113fa))
* **scraper:** bound browser process lifecycles ([#468](https://github.com/groktopus/groktocrawl/issues/468)) ([efdc388](https://github.com/groktopus/groktocrawl/commit/efdc388fc3b101df16186e5b3b1bb31311edb646)), closes [#467](https://github.com/groktopus/groktocrawl/issues/467)
* **scraper:** record tier error metric on smart_scrape raise ([b382978](https://github.com/groktopus/groktocrawl/commit/b382978a9a9db69ca05c71c4f169555bc5834a66))
* **scraper:** sample browser stage metrics on Playwright raise ([fc7fd8c](https://github.com/groktopus/groktocrawl/commit/fc7fd8c1584491a528f22771f39c9dfa1e0920ab))
* **scraper:** sample extraction histogram on empty/failed extractions ([47e7c5c](https://github.com/groktopus/groktocrawl/commit/47e7c5c13fb11bd44be7d9de7b969cc8106b71ee))
* **security:** reject all NAT64 space and redact webhook URLs in logs ([f6fad87](https://github.com/groktopus/groktocrawl/commit/f6fad8798be1d210154460d6cd30e52cf3877b50))
* **security:** reject IPv4-encapsulating IPv6 forms in SSRF guard ([a7db7bf](https://github.com/groktopus/groktocrawl/commit/a7db7bf73b4e35f8fc393c170812b5846c4b2fb3))
* **security:** reject IPv4-mapped IPv6 private addresses in SSRF guard ([6c67dec](https://github.com/groktopus/groktocrawl/commit/6c67dec0c11f6a47e6bb88b609b41cb5a92c009f))
* **security:** unmap Teredo client and NAT64-embedded IPv4 in SSRF guard ([0a92dc7](https://github.com/groktopus/groktocrawl/commit/0a92dc72df0120c022d0b7fe01daa1efe6b64d0c))
* **security:** validate webhook destinations to prevent SSRF ([4cd6e90](https://github.com/groktopus/groktocrawl/commit/4cd6e90a19590995f90d55b07d13a7bec1e1d92b))
* **security:** validate webhook destinations to prevent SSRF ([3889bfa](https://github.com/groktopus/groktocrawl/commit/3889bfa25d3df36699808261b2ab981979a4ddf6))
* **semantic-svc:** resolve 7 mypy errors surfaced by qdrant_client stubs ([1d63dd8](https://github.com/groktopus/groktocrawl/commit/1d63dd81d0d1f227dbd19c4d79243e985eadb6fa))
* **semantic-svc:** resolve 7 mypy errors surfaced by qdrant_client stubs ([cb61034](https://github.com/groktopus/groktocrawl/commit/cb6103413ce7bfa5e3213dca094763813c128d6e))
* **semantic:** bound Qdrant query timeout and degrade to 503 instead of 500 ([#544](https://github.com/groktopus/groktocrawl/issues/544)) ([c34d906](https://github.com/groktopus/groktocrawl/commit/c34d9061c19790dfc3205c5889b1559609fbbe0e))
* **semantic:** narrow Qdrant readiness client type ([ae37711](https://github.com/groktopus/groktocrawl/commit/ae377112e7a43114b8f818039024199a9efd8ee0))
* **semantic:** narrow Qdrant readiness client type ([e96a935](https://github.com/groktopus/groktocrawl/commit/e96a935d9b768513dddfddaf8bddfc3167872898)), closes [#486](https://github.com/groktopus/groktocrawl/issues/486)
* **semantic:** require Qdrant for readiness ([#485](https://github.com/groktopus/groktocrawl/issues/485)) ([3e5ab1a](https://github.com/groktopus/groktocrawl/commit/3e5ab1ac0623c0091b1ecc3036747df43cecf79e))
* skip failed results in research memory cache ([609440d](https://github.com/groktopus/groktocrawl/commit/609440d558863d3d6cd3b833e3a89a290d681e17))
* skip failed results in research memory cache ([0e32c79](https://github.com/groktopus/groktocrawl/commit/0e32c79a470db97d2c32be294d87a476b19eb4ae)), closes [#432](https://github.com/groktopus/groktocrawl/issues/432)
* support disabling llama.cpp template reasoning ([b3a7912](https://github.com/groktopus/groktocrawl/commit/b3a7912cbeddcb22e962511ef3c3671b480d266b))
* support disabling llama.cpp template reasoning ([9140613](https://github.com/groktopus/groktocrawl/commit/91406134f361d6f0159ecf8b2a537f223d3925dc))
* **test:** remove unused variable flagged by vulture ([261368f](https://github.com/groktopus/groktocrawl/commit/261368fa94d371cc9c1a59561d67366bd7457c2c))
* update CLI coverage script to scan routes/ package ([7d2905d](https://github.com/groktopus/groktocrawl/commit/7d2905d1076440fd0bd3a3cd3dbc052f6dba6550))
* update CLI coverage script to scan routes/ package instead of api.py ([d401e17](https://github.com/groktopus/groktocrawl/commit/d401e17445cbd9d3854147c2f034d2204d49d699))
* wait for semantic service readiness ([746ee46](https://github.com/groktopus/groktocrawl/commit/746ee46c1a691c3c398c83e415d9302b3e173a0c))
* wait for semantic service readiness ([37be058](https://github.com/groktopus/groktocrawl/commit/37be05812eacc48f26447f1515f236abdf805f96))
* **webhook:** never leak parser or resolver errors from destination guard ([9a7d799](https://github.com/groktopus/groktocrawl/commit/9a7d799ca060fd55d68d9545d8190e7ab90941cb))
* **webhook:** redact userinfo credentials from webhook URL logs ([d618215](https://github.com/groktopus/groktocrawl/commit/d618215414d7aa513ac10c9bef3290fe2758deed))
* **webhook:** retry transient DNS failures during destination validation ([9828d7b](https://github.com/groktopus/groktocrawl/commit/9828d7ba3ce31573f041de531be32fe4e0c0ff6b))
* **worker:** do not double-count cancelled jobs ([5465541](https://github.com/groktopus/groktocrawl/commit/5465541528fc40df1c2f095d96c1b2613a3b0110))


### Performance Improvements

* add concurrent cache-assisted hybrid retrieval ([a3cfb96](https://github.com/groktopus/groktocrawl/commit/a3cfb9662a67f21b2828acf6542f5e9ee96a47bf))
* add global admission control and end-to-end cancellation ([65a8f22](https://github.com/groktopus/groktocrawl/commit/65a8f220c145f924153dd2a42efc6f268dc793a4))
* add stage-level latency and capacity telemetry ([25b24ca](https://github.com/groktopus/groktocrawl/commit/25b24cae2124f1af2328e24f243035492b6ed2ea))
* **agent:** add concurrent cache-assisted hybrid retrieval planner ([399e4a2](https://github.com/groktopus/groktocrawl/commit/399e4a2ab346ad08d2bede6ed032b52760385499))
* **agent:** add global weighted admission controller and cancel token ([cd3cff7](https://github.com/groktopus/groktocrawl/commit/cd3cff7eb11fa3ff2fc1aa7f8950f116c3531e20)), closes [#531](https://github.com/groktopus/groktocrawl/issues/531)
* **agent:** instrument research stages, caches, streaming, and workload class ([b10ba94](https://github.com/groktopus/groktocrawl/commit/b10ba94590a82375c963485b6ecc4d1aceabc468))
* **agent:** request-scoped source artifact with rerank reuse ([0802c50](https://github.com/groktopus/groktocrawl/commit/0802c502256470a638fd46a412afb5c9bfa28125))
* **agent:** single research-memory lookup per agent request ([d4eaba6](https://github.com/groktopus/groktocrawl/commit/d4eaba69d2b90ea032504b14fccd5c12911a9f99))
* **agent:** wire admission and cancellation into fan-out paths ([f6e9bfc](https://github.com/groktopus/groktocrawl/commit/f6e9bfcbb8b8f76dac83ccda3a1637ef7207d7a6)), closes [#531](https://github.com/groktopus/groktocrawl/issues/531)
* **benchmarks:** add stdlib benchmark harness and checked-in baseline ([2b3fc71](https://github.com/groktopus/groktocrawl/commit/2b3fc71ce1476233df1d73570960ed1bb26a8905))
* eliminate duplicate retrieval, cache, and browser work ([2b96563](https://github.com/groktopus/groktocrawl/commit/2b9656309ed4e62af095cdda4266aca88e4d7028))
* **scraper:** add tier/adapter/cache and browser lifecycle telemetry ([87cf58c](https://github.com/groktopus/groktocrawl/commit/87cf58c040b48f826b481d7c49de549d5309b478))
* **scraper:** browser-tier admission and politeness burst fix ([ae3c625](https://github.com/groktopus/groktocrawl/commit/ae3c625d2c1994e610975911de802400ce1b6bbf)), closes [#531](https://github.com/groktopus/groktocrawl/issues/531)
* **scraper:** lightweight-only contract and awaited cancellation ([bef4b5e](https://github.com/groktopus/groktocrawl/commit/bef4b5e47f673a98549eff47383b49cf05352926))
* **worker:** bound batch scrape task creation with a worker pool ([2f1636a](https://github.com/groktopus/groktocrawl/commit/2f1636a589813248880cfb249f5bb5cfe0190e99))


### Documentation

* add ADR-0049 for research-memory compatibility, freshness, and SWR ([b6a100a](https://github.com/groktopus/groktocrawl/commit/b6a100a6f3227ca4adabce5ee9a092a729f4add0)), closes [#529](https://github.com/groktopus/groktocrawl/issues/529)
* add contribution intake and Now/Next/Later roadmap ([adaa195](https://github.com/groktopus/groktocrawl/commit/adaa1952eb558a1b52e1157afdc77e5ea5fe9fd8))
* add contribution intake and Now/Next/Later roadmap ([23f3ab5](https://github.com/groktopus/groktocrawl/commit/23f3ab532d215628ed164b3551a97f14f9213c48))
* add graphify query tuning guidance to AGENTS.md ([966643b](https://github.com/groktopus/groktocrawl/commit/966643b112a2a1dd7c452bbd8fd2f7eda6ff1f85))
* add graphify query tuning guidance to AGENTS.md ([e380a8d](https://github.com/groktopus/groktocrawl/commit/e380a8d529fcb66304008ead8962bd8c777eaa84))
* add release and dependency-update PR triage routine ([104def5](https://github.com/groktopus/groktocrawl/commit/104def50d430fa9668f3669ae39b6ebf07fa3fe3))
* add release and dependency-update PR triage routine ([486a9c9](https://github.com/groktopus/groktocrawl/commit/486a9c9727496e15953164552103d658cfda0604))
* add stage telemetry ADR, metric inventory, and dashboard panels ([09b35c4](https://github.com/groktopus/groktocrawl/commit/09b35c46d5e1e5027f4c56de1657dbe2505b4ca0))
* ADR-0051 admission control, observability, public surface ([0a7fa3e](https://github.com/groktopus/groktocrawl/commit/0a7fa3e4987101d01d3f8aa560ac99e95db3b2ae)), closes [#531](https://github.com/groktopus/groktocrawl/issues/531)
* **adr:** make async-job durability an explicit deployment and roadmap decision ([#521](https://github.com/groktopus/groktocrawl/issues/521)) ([608486f](https://github.com/groktopus/groktocrawl/commit/608486f94c9b5b6c1341e067f63de0dc05ae2398))
* **adr:** remove private deployment identifier from ADR-0026 ([838ce9f](https://github.com/groktopus/groktocrawl/commit/838ce9f888ee28694730691ee427253e25692d21))
* **adr:** remove private deployment identifier from ADR-0026 ([7a1c75f](https://github.com/groktopus/groktocrawl/commit/7a1c75f9f4f732bf087ae7d57bd2e5d3b68c34f5))
* **agent:** clarify async job durability ([#462](https://github.com/groktopus/groktocrawl/issues/462)) ([8f37b7b](https://github.com/groktopus/groktocrawl/commit/8f37b7b8a07e0f76071c4528f5553dae94ef46fd))
* align emergency runbook trigger list with self-merge note ([e725c72](https://github.com/groktopus/groktocrawl/commit/e725c7230cedd4fc1752662612b68c458de0f7b8)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* **benchmarks:** fix percentile interpolation docstring ([f42aae0](https://github.com/groktopus/groktocrawl/commit/f42aae08ad3c5857a2f01d45c78b51f75ddb2977))
* clarify CLI authentication configuration ([a0eca83](https://github.com/groktopus/groktocrawl/commit/a0eca831febb0fa8f0842207add8e5676d1dcc51))
* clarify CLI authentication configuration ([ab247f7](https://github.com/groktopus/groktocrawl/commit/ab247f7a147165f2438dabb239f14ebd77946ec8))
* correct search-client mutation-review kill claims ([09921fc](https://github.com/groktopus/groktocrawl/commit/09921fcc5028ee5d3acd8266f15de57c99eba6d9))
* correct triage summary kill claims ([947bfb5](https://github.com/groktopus/groktocrawl/commit/947bfb5b4127a3ef51f16b336e0d89d3f85b1aee))
* document dedup/retry metrics and ADR-0050 ([a1af8ba](https://github.com/groktopus/groktocrawl/commit/a1af8ba5f82dd034f7332938397249876a38036a))
* document hybrid blend diversity floor in ADR-0052 ([a275777](https://github.com/groktopus/groktocrawl/commit/a2757774d6fafd271a213eb60412552bb3afe826))
* document hybrid retrieval planner and add benchmark script ([b542bdb](https://github.com/groktopus/groktocrawl/commit/b542bdb3704cb82c2c8b9d0a46a7b25be866cbf8))
* document PR review loop and merge bypass for agents ([9b42178](https://github.com/groktopus/groktocrawl/commit/9b421785e633bd030182916dd41a5161db97b34a))
* document PR review loop and merge bypass for agents ([a77564a](https://github.com/groktopus/groktocrawl/commit/a77564a721c4771b402268418ab320505d8742e8))
* mark vision as historical ([1719843](https://github.com/groktopus/groktocrawl/commit/17198436b6b26531c91e343607712b091970b2e9))
* mark vision as historical ([00dcbf3](https://github.com/groktopus/groktocrawl/commit/00dcbf3631570c83218cd6f4500c0f7225052dd1))
* note AshbyHQ posting API and expanded parse formats ([3a3cb34](https://github.com/groktopus/groktocrawl/commit/3a3cb3415727b2a60d9ccb7a21058f73bf806f87))
* note AshbyHQ posting API and expanded parse formats ([0f1a9fc](https://github.com/groktopus/groktocrawl/commit/0f1a9fc4d784a05fc2bbb1fd7c1cf7362ada6436))
* note maintainer self-merge in emergency runbook ([d185b6a](https://github.com/groktopus/groktocrawl/commit/d185b6a74f95be013e98d49df61efc2032b433f6))
* note maintainer self-merge in emergency runbook ([74bac62](https://github.com/groktopus/groktocrawl/commit/74bac62d72c96e8209db94e325264ca02dd88ca5)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* note maintainer self-merge in emergency runbook ([#520](https://github.com/groktopus/groktocrawl/issues/520)) ([d185b6a](https://github.com/groktopus/groktocrawl/commit/d185b6a74f95be013e98d49df61efc2032b433f6))
* note minimum graphify version for --context flag ([5bc186d](https://github.com/groktopus/groktocrawl/commit/5bc186db5c0563de35a7262cdc53dcc699c5ab3e))
* overhaul documentation and add drift checks ([#422](https://github.com/groktopus/groktocrawl/issues/422)) ([8473fdd](https://github.com/groktopus/groktocrawl/commit/8473fdd170e66c9cb3d5f499dd9f31696a1d3bca))
* place roadmap under docs/ so docs-only PRs stay docs-only ([156239a](https://github.com/groktopus/groktocrawl/commit/156239af84191c29e14ad28525e93ad93f770a53))
* promote ADR-0046 status to accepted ([6eebdf7](https://github.com/groktopus/groktocrawl/commit/6eebdf7b3882466da35038ef29ddd9935976348c))
* promote ADR-0046 status to accepted ([0b58e34](https://github.com/groktopus/groktocrawl/commit/0b58e34dc0d949574148b08b5350d56062357a1f))
* **readme:** fix quickstart examples and required auth token ([9ebcb3c](https://github.com/groktopus/groktocrawl/commit/9ebcb3cef94eefbb45a3171c6d35bc2aa4f42adf))
* **readme:** restructure quickstart into Choose your path ([cf9c4dc](https://github.com/groktopus/groktocrawl/commit/cf9c4dc836b9d4c4f3d935e20dcffc81488df008))
* **readme:** restructure quickstart into Choose your path ([#472](https://github.com/groktopus/groktocrawl/issues/472)) ([f6aa8cd](https://github.com/groktopus/groktocrawl/commit/f6aa8cd8ebf287f70508a0b3ccbd3ae88d761d59))
* register MCP_ALLOWED_HOSTS/MCP_ALLOWED_ORIGINS in public-surface inventory ([a305a1e](https://github.com/groktopus/groktocrawl/commit/a305a1e10662260c6ed506dbc969718e15fc965f))
* verify observability artifact chain ([#449](https://github.com/groktopus/groktocrawl/issues/449)) ([3c13dfc](https://github.com/groktopus/groktocrawl/commit/3c13dfc61293d41a67eca1bf4a5ad8d5ff129c47))


### CI/CD

* add diagnostic critical journey smoke ([#446](https://github.com/groktopus/groktocrawl/issues/446)) ([2c06ea8](https://github.com/groktopus/groktocrawl/commit/2c06ea859e391f6e305a5aa5582656cdcf00344e))
* bump actions/setup-python from 6 to 7 ([6935654](https://github.com/groktopus/groktocrawl/commit/6935654c6ec7bfd6af7c50a82ca8b055aa15c8c9))
* bump actions/setup-python from 6 to 7 ([5b6f1d3](https://github.com/groktopus/groktocrawl/commit/5b6f1d3b81571131dbdb9303f5b860b5271051b4))
* bump actions/upload-artifact from 4 to 7 ([19ea3a7](https://github.com/groktopus/groktocrawl/commit/19ea3a766f4f26e765d5e0e1dcf74eea8df7a405))
* bump actions/upload-artifact from 4 to 7 ([553da2c](https://github.com/groktopus/groktocrawl/commit/553da2ced21f7b11c8f1ecf16a4919457264b957))
* bump Factory-AI/droid-action ([c40a056](https://github.com/groktopus/groktocrawl/commit/c40a056e7c2e68b33508fc81c911382be97a91f0))
* bump Factory-AI/droid-action from 7c7bfea2aa3bb7ea87579402cc1d89dbcf6b13b3 to 2041e7cc36d1390346bdaa6616e4bf7ac6b96b27 ([3835742](https://github.com/groktopus/groktocrawl/commit/383574243fc395e77be5f4d6d5daae722dea1601))
* compile fixture probes exactly ([4cb1577](https://github.com/groktopus/groktocrawl/commit/4cb157798f5f6c5d2c7bd6e8bf848f1d516c5410))
* **docker:** address droid-review P1/P2 on runtime lanes ([#494](https://github.com/groktopus/groktocrawl/issues/494)) ([fafd1e0](https://github.com/groktopus/groktocrawl/commit/fafd1e0f53a1a810cf10357a8269bd087a5be854))
* **docker:** address droid-review P1/P3 on fork guard ([#483](https://github.com/groktopus/groktocrawl/issues/483)) ([eacbc9c](https://github.com/groktopus/groktocrawl/commit/eacbc9c375890326a03a749a5f10327095028531))
* **docker:** drop dead parse deps from integration lane ([f073df9](https://github.com/groktopus/groktocrawl/commit/f073df9cf83523449e3efe4abdf5bf13063908b7))
* **docker:** drop dead parse deps from integration lane ([d6815e9](https://github.com/groktopus/groktocrawl/commit/d6815e9943110bfa3dabc33d6d0d23100acf17f8))
* **docker:** fix mcp-svc build-and-push context ([8d2ad15](https://github.com/groktopus/groktocrawl/commit/8d2ad1599e6683a4a523b075ec667ca1325551dd))
* **docker:** fix mcp-svc build-and-push context ([69ebf00](https://github.com/groktopus/groktocrawl/commit/69ebf003378754a6b4ede1fc3f795845ff19351b))
* **docker:** fix mcp-svc build-and-push context ([#494](https://github.com/groktopus/groktocrawl/issues/494) follow-up) ([f736a5b](https://github.com/groktopus/groktocrawl/commit/f736a5b9d278c4b85c045b8e32f03e4d61419ba4))
* **docker:** fix mcp-svc build-and-push context ([#494](https://github.com/groktopus/groktocrawl/issues/494)) ([c048977](https://github.com/groktopus/groktocrawl/commit/c0489772981f8c44f0acf830b0e4616098edd513))
* **docker:** guard self-hosted integration against fork PRs ([#483](https://github.com/groktopus/groktocrawl/issues/483)) ([9d4723c](https://github.com/groktopus/groktocrawl/commit/9d4723c8e54e9ec2c1e6c6799c1cae2b15cae64a))
* **docker:** guard self-hosted integration against fork PRs ([#483](https://github.com/groktopus/groktocrawl/issues/483)) ([43608a8](https://github.com/groktopus/groktocrawl/commit/43608a849b4814e6d7ee5a42e152a25f70b9a222))
* **docker:** publish parse-svc and mcp-svc images ([f752bbd](https://github.com/groktopus/groktocrawl/commit/f752bbd08593bb86d0d2f29f6a3327a301721431))
* **docker:** service-local PR builds and remove duplicate --build ([#494](https://github.com/groktopus/groktocrawl/issues/494)) ([323d6f0](https://github.com/groktopus/groktocrawl/commit/323d6f07702914129e6a2f3fb119ad56460d41c9))
* **docker:** service-local PR builds, remove duplicate --build ([#494](https://github.com/groktopus/groktocrawl/issues/494)) ([e029a06](https://github.com/groktopus/groktocrawl/commit/e029a063ebc19068a8894dcb2bbaf1d5d2fd3236))
* **docker:** tolerate unpublished images in the pull lane ([#494](https://github.com/groktopus/groktocrawl/issues/494)) ([e981516](https://github.com/groktopus/groktocrawl/commit/e981516a9f837234597207a552c71f9245aa231b))
* enforce critical journey smoke gate ([#447](https://github.com/groktopus/groktocrawl/issues/447)) ([c407aa0](https://github.com/groktopus/groktocrawl/commit/c407aa0a4f79cb901d1ee1a43dbaaba304fa7c69))
* enforce required QA checks and review policy on main ([9277c29](https://github.com/groktopus/groktocrawl/commit/9277c293e248e6cba0d94df5e04eccff9eff1484))
* enforce required QA checks and review policy on main ([66e44fe](https://github.com/groktopus/groktocrawl/commit/66e44fea6585b002deccfe97e637270c5583a7e1)), closes [#499](https://github.com/groktopus/groktocrawl/issues/499)
* gate source-backed fixture contracts early ([5e88b9c](https://github.com/groktopus/groktocrawl/commit/5e88b9ce5aacebaefc8b851ff847384871a820f0))
* integrate dependency twin validation ([e3bbf6f](https://github.com/groktopus/groktocrawl/commit/e3bbf6f97ac3529e0fc001e1f6ebb3d0ef6af5b3))
* integrate dependency twin validation ([7f86d0d](https://github.com/groktopus/groktocrawl/commit/7f86d0dc76f4244fd8010e4fcf4266427d93ceec))
* make dependency hygiene blocking ([4f15fb2](https://github.com/groktopus/groktocrawl/commit/4f15fb2fc2d45d8e0650ec4de35424d8102515d9))
* make dependency hygiene blocking ([4c0c5c3](https://github.com/groktopus/groktocrawl/commit/4c0c5c341b6943674fb6055434780c5192ff9e3e)), closes [#471](https://github.com/groktopus/groktocrawl/issues/471)
* **parse-svc:** install firecrawl-anydoc in the integration test runner ([fc055a8](https://github.com/groktopus/groktocrawl/commit/fc055a853204f276c7d35378822c23c2630ae72d))
* **parse-svc:** install pypdf in the integration test runner ([da18a7e](https://github.com/groktopus/groktocrawl/commit/da18a7ef9054c2cf4990271bfa4e9672dd3f3d25))
* preserve integration failure diagnostics ([0106eba](https://github.com/groktopus/groktocrawl/commit/0106ebaf2ce3fb1cd131cc7839cdd4d140a3398a))
* provide MCP token for integration stack ([030176e](https://github.com/groktopus/groktocrawl/commit/030176e648a87271dc8a90650777a33522d566d8))
* publish parse-svc and mcp-svc images ([5d4ca1c](https://github.com/groktopus/groktocrawl/commit/5d4ca1c604af000c104ed55a97ae8610ebefc703))
* reset persisted test memory before integration ([16f557e](https://github.com/groktopus/groktocrawl/commit/16f557e03f1fcbcfb21f1f89b2b060bd2a5f850a))
* resolve twin review findings ([7b26d55](https://github.com/groktopus/groktocrawl/commit/7b26d55ef7ca1b13b18825f321c9e8aac53e538b))
* **review:** trigger re-review on synchronize pushes ([a113198](https://github.com/groktopus/groktocrawl/commit/a113198ab0d1e5580be39e84f226899ffdd9d9ae))
* **review:** trigger re-review on synchronize pushes ([ca2477b](https://github.com/groktopus/groktocrawl/commit/ca2477bd8bd0ab66708606b09e626ef55d9c09c8))
* **review:** use DeepSeek V4 Flash for droid auto review ([d787c24](https://github.com/groktopus/groktocrawl/commit/d787c24ccd0564069af9714cd742e60a3d1fdd44))
* **review:** use DeepSeek V4 Flash for droid auto review ([3cb5724](https://github.com/groktopus/groktocrawl/commit/3cb57243e47637bbaf6af7449ae148b1ffd3b9ae))
* route answer evals through host lane ([b1a9a97](https://github.com/groktopus/groktocrawl/commit/b1a9a97ebb1a3e17fe5f923e37ed3aab8b69ec24))
* run deterministic fast tests on every change ([0bbbf24](https://github.com/groktopus/groktocrawl/commit/0bbbf244d56abe84aec66827b55c1afd60c6aeaa))
* separate hosted and compose twin contracts ([93dc54e](https://github.com/groktopus/groktocrawl/commit/93dc54eaab58180ede01bc7028b1f23b62af7da9))
* serialize shared-runner integration jobs ([#453](https://github.com/groktopus/groktocrawl/issues/453)) ([946e6ed](https://github.com/groktopus/groktocrawl/commit/946e6ede91fab7f1eadb54adcf7841309ad4aff1))
* skip Docker integration for docs-only pull requests ([8f6b280](https://github.com/groktopus/groktocrawl/commit/8f6b28030186cbe6d80617dfe295efa04787122c))
* skip Docker integration for docs-only pull requests ([1781908](https://github.com/groktopus/groktocrawl/commit/17819087d640a95adff398c2b55cbeba946d9006))


### Refactoring

* **agent:** unify research execution ([#461](https://github.com/groktopus/groktocrawl/issues/461)) ([d3e94d8](https://github.com/groktopus/groktocrawl/commit/d3e94d82d86385065d490f0df98c420322ba8eb8))
* **webhook:** share destination validation between job and monitor paths ([80ec386](https://github.com/groktopus/groktocrawl/commit/80ec386c3e6b93a5c00fe4101ebb3e07048da243))


### Tests

* add bounded mutation-testing pilot harness for search-client slice ([ae4299b](https://github.com/groktopus/groktocrawl/commit/ae4299b78cb7f6bc04a9d9daf560823de35725fb))
* add grounded answer evaluation harness ([9f4446c](https://github.com/groktopus/groktocrawl/commit/9f4446c20e75bd23d89030458f82a682d44d1820))
* add grounded answer evaluation harness ([e3186e2](https://github.com/groktopus/groktocrawl/commit/e3186e2759c3348ac4a5a7b9db0314d561f56f3a))
* admission budgets, cancellation teardown, batch parallelism, politeness ([5f6f4ec](https://github.com/groktopus/groktocrawl/commit/5f6f4eceaa08dd6b96be5eae23c6cb3aaf24bf8c)), closes [#531](https://github.com/groktopus/groktocrawl/issues/531)
* **agent:** cover degraded-lightweight fallback and answer URL dedup ([51f9a1e](https://github.com/groktopus/groktocrawl/commit/51f9a1e834d2d574cf14dcfe660822705dfa6304))
* **agent:** cover hybrid blend dedup, malformed URLs, and diversity floors ([a235b73](https://github.com/groktopus/groktocrawl/commit/a235b73a8ce68752312ab612806eb5ad12006d9c))
* **agent:** cover hybrid retrieval planner and call-site delegation ([4de1ab3](https://github.com/groktopus/groktocrawl/commit/4de1ab3d4b7f13a91fec7f4cc7904fbadedcfc46))
* **agent:** cover research-memory compatibility, freshness, SWR, and sweep ([21abf71](https://github.com/groktopus/groktocrawl/commit/21abf71033d7c799e58695b33c5524e961c43563)), closes [#529](https://github.com/groktopus/groktocrawl/issues/529)
* **agent:** cover run_answer_stream rerank reuse ([a0af90f](https://github.com/groktopus/groktocrawl/commit/a0af90f4841145ce3c92f0ab503f91a4a337081e))
* **agent:** cover SWR compact sources and single-flight refresh ([f743f76](https://github.com/groktopus/groktocrawl/commit/f743f767028735fe20f107ea06d3038a1ac938c2))
* bounded mutation-testing pilot on search policy ([bf0ca66](https://github.com/groktopus/groktocrawl/commit/bf0ca66f91ff81b0e29d5da9fd585562dcef04c1))
* cover issue 530 dedup, reuse, and browser lifecycle ([a3a3cb3](https://github.com/groktopus/groktocrawl/commit/a3a3cb3f871c02d1e40ed676fac024f313a61748))
* **crawler:** cover cancellation branches in the fast lane ([0041def](https://github.com/groktopus/groktocrawl/commit/0041def33a804153bac05352909e2c9e0f420513))
* finalize search-client mutation-pilot evidence, report, and ADR-0057 ([64b44d0](https://github.com/groktopus/groktocrawl/commit/64b44d05e7111a6458c9d659133f6502d1389b9d))
* govern pytest outcome lifecycle ([#509](https://github.com/groktopus/groktocrawl/issues/509)) ([f6e8a28](https://github.com/groktopus/groktocrawl/commit/f6e8a2802f0a89b1f9ed0fee642f779ec4ac5812)), closes [#502](https://github.com/groktopus/groktocrawl/issues/502)
* harden deferred search-client oracle gaps ([027de90](https://github.com/groktopus/groktocrawl/commit/027de90ddb90075b08c80ac1444d7fdd4ea4d802))
* harden evaluation artifacts and baselines ([2f37b5e](https://github.com/groktopus/groktocrawl/commit/2f37b5e71f557e78f7c8ba99dd5f9e527904dca6))
* isolate hosted socket dependency ([68ab87f](https://github.com/groktopus/groktocrawl/commit/68ab87f2414bdd485482beb18376ec4307e9d3fe))
* make GitHub adapter coverage deterministic ([41a70f5](https://github.com/groktopus/groktocrawl/commit/41a70f530dd7cd19acb95056d7f20fb643a6ba3c))
* **mcp-svc:** fix _default_allowed_test_host exact host:port handling ([87b90f2](https://github.com/groktopus/groktocrawl/commit/87b90f29cad901f8604e12bf519100e93e6f733b))
* **mcp-svc:** isolate origin-derivation test, derive allowed Host from allowlist ([e14883c](https://github.com/groktopus/groktocrawl/commit/e14883c9a36a22e1eab67812fa0be0bef9747f36))
* **metrics:** cover browser session expired lifecycle paths ([80b0489](https://github.com/groktopus/groktocrawl/commit/80b0489413f0bb0fc553a5cece012e8a79278ade))
* **metrics:** cover semantic OpenMetrics renderer ([ff3a938](https://github.com/groktopus/groktocrawl/commit/ff3a9386844bc02abb1e5c66a68c58bc6a7d020e))
* **metrics:** cover semantic OpenMetrics renderer ([90d0551](https://github.com/groktopus/groktocrawl/commit/90d05516e2b82cfd531ec996cba3cf15e200f779))
* **metrics:** cover stage metrics, browser lifecycle branches, and harness ([eb67ce2](https://github.com/groktopus/groktocrawl/commit/eb67ce24aea74df5c4fdd2c8d68e80c3543e24d4))
* **metrics:** cover stage-metric error and stream telemetry paths ([a8edb1b](https://github.com/groktopus/groktocrawl/commit/a8edb1ba6c857d705a9b3b2f2f795f202b0904fe))
* **metrics:** cover TTFB, tier error, and browser create-failure regressions ([79d03b9](https://github.com/groktopus/groktocrawl/commit/79d03b9471204797d907cba1cf0a65dc1f3d4204))
* **metrics:** run service-module metric tests in unit lane ([a796352](https://github.com/groktopus/groktocrawl/commit/a7963525bc4a6c47e6cc0de32ae9f80a80522015))
* **parse-svc:** add real .docx/.pptx/.xlsx fixtures ([3c8cb0a](https://github.com/groktopus/groktocrawl/commit/3c8cb0aaa185ce8fd31ae1068bdb17332fa30242))
* quarantine live Gutenberg probes ([abd6ab5](https://github.com/groktopus/groktocrawl/commit/abd6ab5a50c9a005e56da10a3b0480a264a36344))
* quarantine live Gutenberg probes ([b9bfbbe](https://github.com/groktopus/groktocrawl/commit/b9bfbbe2299a5f320efae6ec68312292bee23768))
* remove live-search dependency from cross-session isolation ([72a01fa](https://github.com/groktopus/groktocrawl/commit/72a01fa0c68da52935c71e4fc994a6bc73b3c2f5))
* triage search-client survivors and harden Retry-After boundary gaps ([9a7b881](https://github.com/groktopus/groktocrawl/commit/9a7b881c377a5f82c0ebfaba1dc0cf1d586df4da))
* **url:** assert permanent rejection for overlong hostnames ([#508](https://github.com/groktopus/groktocrawl/issues/508)) ([1fc36f5](https://github.com/groktopus/groktocrawl/commit/1fc36f5d5b164922c47fbf2e3761430f67f8b53e))


### Chores

* **coverage:** exempt cross-container high-risk files from integration gate ([05f63db](https://github.com/groktopus/groktocrawl/commit/05f63db48c1a1afbc1a28fa4d639d3f4044ef374))
* **deps:** regenerate lockfile for cloakbrowser 0.5.3 ([9181fce](https://github.com/groktopus/groktocrawl/commit/9181fcefaa8c6d72716911b6dfac59415a1e8321))
* **deps:** regenerate lockfile for qdrant-client &lt;1.20.0 ([30b7f00](https://github.com/groktopus/groktocrawl/commit/30b7f007f8afda35118a2c0b26c7b64320c51f29))
* ignore parallel-mode coverage data files ([67630b0](https://github.com/groktopus/groktocrawl/commit/67630b07b34f017d60e3cbe96c01eaedf4787682))
* ignore parallel-mode coverage data files (.coverage*) ([e0ed4c2](https://github.com/groktopus/groktocrawl/commit/e0ed4c223a5f1d81103005b0a3766941cf948213))
* remove search-svc fixture, update docs from SearXNG to SlopSearX ([#405](https://github.com/groktopus/groktocrawl/issues/405)) ([fd556b1](https://github.com/groktopus/groktocrawl/commit/fd556b120a05288b94d4249b755ecd573e3c36d0))

## [0.12.1](https://github.com/groktopus/groktocrawl/compare/v0.12.0...v0.12.1) (2026-07-06)


### Bug Fixes

* llm-svc fixture handles output_schema with schema-conformant responses ([#404](https://github.com/groktopus/groktocrawl/issues/404)) ([5e62cff](https://github.com/groktopus/groktocrawl/commit/5e62cff76bd4ba5ad29c149bf164e092df4e3b5a))
* resolve integration test failures in output_schema and deepen tests ([8276bc5](https://github.com/groktopus/groktocrawl/commit/8276bc5e47f2c5011ac4f650a99aaa02f1521165))

## [0.12.0](https://github.com/groktopus/groktocrawl/compare/v0.11.0...v0.12.0) (2026-07-05)


### Features

* add batch query/store endpoints and comprehensive M2 memory integration tests ([f878aaa](https://github.com/groktopus/groktocrawl/commit/f878aaa4ea1a864878451c9c1cc572feeab7a46a))
* add plan and execute endpoints for plan-consent flow ([95902a8](https://github.com/groktopus/groktocrawl/commit/95902a86028e60e70ba80533f2c84f098dd81dab))
* add plan execute tests (VAL-PLN-006/007/008/012) and deepen action refactor ([05ac347](https://github.com/groktopus/groktocrawl/commit/05ac34712121358f22570793b42c71be915db797))
* add plan models, planner improvements, and plan endpoint tests ([ba60e62](https://github.com/groktopus/groktocrawl/commit/ba60e62db7b8a59cc204e16dfbd1b81c20807355))
* add session protocol Pydantic models with validation ([22c74e1](https://github.com/groktopus/groktocrawl/commit/22c74e1f2aa852859be297bb274359dc17fd2455))
* add session resolve endpoint and enhance SessionManager ([d8de0e6](https://github.com/groktopus/groktocrawl/commit/d8de0e6aaaaffb3057290a6f47eea42a35b328cf))
* Agent-Native Research Platform (closes [#387](https://github.com/groktopus/groktocrawl/issues/387)-[#393](https://github.com/groktopus/groktocrawl/issues/393)) ([07edb27](https://github.com/groktopus/groktocrawl/commit/07edb27536abbcc9a0e64fdac838bb4df21a655f))
* complete compact citations feature (M1) ([833e584](https://github.com/groktopus/groktocrawl/commit/833e5848d611f957f1734046ce0b4f53e57b6f25))
* hybrid semantic cache with Valkey + Qdrant research memory (M2) ([ed370f7](https://github.com/groktopus/groktocrawl/commit/ed370f744349202112e56fdfb5d8af5dc31313a8))
* implement m5-mcp-client HTTP client wrapping all 17 agent-svc endpoints ([f6ec080](https://github.com/groktopus/groktocrawl/commit/f6ec080106717ab7e3e1441b365cff8b18ca765f))
* implement m5-mcp-server-core with 17 GroktoCrawl MCP tools ([7f7c09b](https://github.com/groktopus/groktocrawl/commit/7f7c09ba224e78203a5add8432937c1005df26fc))
* MCP server — expose GroktoCrawl through Model Context Protocol (Phase 5) ([5d6b763](https://github.com/groktopus/groktocrawl/commit/5d6b7639f92e1e0a5d467c3b3e747e7b4345bba5)), closes [#393](https://github.com/groktopus/groktocrawl/issues/393)
* **mcp-svc:** Docker deployment with python:3.12-slim, pinned deps, health endpoint ([67c67e0](https://github.com/groktopus/groktocrawl/commit/67c67e06c060bbb0833fc1e2389629bca2f33eac))
* **mcp-svc:** enhance groktocrawl_client with structured error handling and missing tool methods ([d077c6a](https://github.com/groktopus/groktocrawl/commit/d077c6ac54e1311ac016a89e375c4822506afd04))
* **mcp-svc:** integration tests for MCP server concurrent clients, edge cases, and cross-flows ([3be5346](https://github.com/groktopus/groktocrawl/commit/3be534670febb3720185a3038eab849992b5dbbf))
* **mcp-svc:** rewrite session_store.py as async generic TTL session store with asyncio.Lock ([14c7a0c](https://github.com/groktopus/groktocrawl/commit/14c7a0c29ef60b60332d2cbefd374f34ed2842ad))
* plan-consent and depth injection (Phase 3) ([81d69d3](https://github.com/groktopus/groktocrawl/commit/81d69d3039262cdb52be25f7024b66adcf7c42c9))
* research memory — cross-session caching (Phase 4) ([211e64e](https://github.com/groktopus/groktocrawl/commit/211e64e2e186af3569f58982ee4ef0f0dd61399c)), closes [#391](https://github.com/groktopus/groktocrawl/issues/391)
* structured output schema and compact citations (Phase 1) ([5ba02c8](https://github.com/groktopus/groktocrawl/commit/5ba02c8dc1b741cf4102e597c267c95dd8a3d811))
* upgrade LLM structured output with empty-schema handling and streaming schema support ([0795e33](https://github.com/groktopus/groktocrawl/commit/0795e330e33aa21bf71f2ccb38f8e8535ca56e62))
* Valkey-backed session store with HSET meta/refs, HINCRBY step counter, per-session locking ([89db6cb](https://github.com/groktopus/groktocrawl/commit/89db6cb7d61429b43f66462ce2640a2179ee7c5a))
* wire output_schema into answer and agent endpoints with schema alias support ([8e19eac](https://github.com/groktopus/groktocrawl/commit/8e19eacec7459cbe73c4ff6db0770c909cc99efb))
* wire ResearchMemory into agent pipeline with force_fresh and per-user scoping ([1e7810b](https://github.com/groktopus/groktocrawl/commit/1e7810b723bfd80f855711339d699b1376485604))
* wire up Deep Research button in web portal ([58e7d7f](https://github.com/groktopus/groktocrawl/commit/58e7d7f972b58d20d28ca22be65f15b6565cd356))
* wire up Deep Research button in web portal ([58e7d7f](https://github.com/groktopus/groktocrawl/commit/58e7d7f972b58d20d28ca22be65f15b6565cd356))
* wire up Deep Research button in web portal ([29d849f](https://github.com/groktopus/groktocrawl/commit/29d849f4cbdccc742c8bff37f9773572508f4dfa))


### Bug Fixes

* add CLI coverage exemptions for 13 new agent-native endpoints ([0d452a4](https://github.com/groktopus/groktocrawl/commit/0d452a4c42232665104ae0039690c6418d0070d0))
* add poppler-utils to parse-svc Dockerfile for OCR support ([#386](https://github.com/groktopus/groktocrawl/issues/386)) ([2c943ca](https://github.com/groktopus/groktocrawl/commit/2c943ca56b85c723bb49b13289b0ad90000e0e35)), closes [#383](https://github.com/groktopus/groktocrawl/issues/383)
* add rate-limit retry for answer endpoint in VAL-CROSS-005 test ([66078dd](https://github.com/groktopus/groktocrawl/commit/66078dd4f1f4090e1150bb2145980d2717658fd5))
* add webhook, stream, and modify_query support to plan execute ([a83e698](https://github.com/groktopus/groktocrawl/commit/a83e69865551aa092854a4f1b65453e8ac9b103b))
* address droid review — sticky mode, CSS selector, phase indicator ([8b8c6b6](https://github.com/groktopus/groktocrawl/commit/8b8c6b6c8c174e1fe81c73a58d3c73a506735293))
* apply _apply_citation_style in non-streaming and streaming agent paths ([e16f1ea](https://github.com/groktopus/groktocrawl/commit/e16f1ea806585c3c170d7f4791db67d997ddfad5))
* CI integration test failures — rate limit, schema alias, worker test ([ab42fa4](https://github.com/groktopus/groktocrawl/commit/ab42fa4aa6d1971af893536a2ed443d6becc3afa))
* differentiate session step ValueError types (404/409/400) ([3dca580](https://github.com/groktopus/groktocrawl/commit/3dca580e026675411d021019b3abbde93bedad61))
* exclude test files from jscpd duplicate code detection ([751f4b5](https://github.com/groktopus/groktocrawl/commit/751f4b5bcad66e610516b4d2a65d15c943ea0bc6))
* force browser tier when --format images requested so raw_html is available ([47f9bfb](https://github.com/groktopus/groktocrawl/commit/47f9bfb76bef9d8e2dfb209b5825363448ca0ed5)), closes [#378](https://github.com/groktopus/groktocrawl/issues/378)
* force browser tier when --format images requested so raw_html is available ([#380](https://github.com/groktopus/groktocrawl/issues/380)) ([eeab12a](https://github.com/groktopus/groktocrawl/commit/eeab12a373685cb47096c06484cbd0ddf06d3ae9))
* guard against empty choices array in LLM SSE stream ([#385](https://github.com/groktopus/groktocrawl/issues/385)) ([9884bc8](https://github.com/groktopus/groktocrawl/commit/9884bc8cffec62a5b954dd3f6d5cf3f64fb76dbb))
* integration test failures — answer output_schema, deepen ref resolution ([4e75de4](https://github.com/groktopus/groktocrawl/commit/4e75de4de993eda98fe8d95ceeba886658dd74c3))
* make full pipeline test resilient to SearXNG rate limiting ([9697dd7](https://github.com/groktopus/groktocrawl/commit/9697dd7cd57646b6515a0045ce3d1bada9bb313e))
* make session step lock use async backoff to avoid blocking FastAPI event loop ([b510703](https://github.com/groktopus/groktocrawl/commit/b5107036f40d9af0342c4bdd3cf68d66aaef1c46))
* **mcp-svc:** close 3 MCP tool gaps — JSON responses, isError:true, missing tools ([eced9a8](https://github.com/groktopus/groktocrawl/commit/eced9a84f5d6cfc98d49fd74b93bcc516e552f2e))
* **mcp-svc:** handle error:null in agent-svc responses, don't raise isError for falsy errors ([58a4e60](https://github.com/groktopus/groktocrawl/commit/58a4e6086748592616210cc5965292698fa073c8))
* P0 review fixes — compact citation source preservation, uvicorn dep, json_object test ([9f29b6b](https://github.com/groktopus/groktocrawl/commit/9f29b6b74814975015d380bb8ce6430349bc982d))
* P1+P2 review fixes — citation regex, lock lease, scope, rate limiting ([7880215](https://github.com/groktopus/groktocrawl/commit/788021545f82717ed3307bc6c0e9553cc294ba74))
* resolve integration test failures in output_schema and deepen tests ([8276bc5](https://github.com/groktopus/groktocrawl/commit/8276bc5e47f2c5011ac4f650a99aaa02f1521165))
* resolve mypy type errors in models.py, research.py, and session_store.py ([9ccd862](https://github.com/groktopus/groktocrawl/commit/9ccd862a901e9bbf2646e92839c549fd3eaff479))
* revert test_memory_ttl_configurable to snake_case keys ([71abbbd](https://github.com/groktopus/groktocrawl/commit/71abbbdfb13a007b458956b54eb85fff771286e5))
* structural fallback in html_to_markdown for SPA-heavy sites ([70bf93c](https://github.com/groktopus/groktocrawl/commit/70bf93c644d58cdd001c5e144dcb4a706d369dfe))
* structural fallback in html_to_markdown for SPA-heavy sites ([938ff5b](https://github.com/groktopus/groktocrawl/commit/938ff5b99d10ac45c901112daebb08b6f2404175)), closes [#361](https://github.com/groktopus/groktocrawl/issues/361)
* switch json_schema to json_object for DeepSeek compatibility, fix cache replay tokens with schema ([6cf8828](https://github.com/groktopus/groktocrawl/commit/6cf88288eb1952f3388df33d6e7055c5fbef0e1a))
* three M2 validation gaps — DELETE 404, prompt min_length, batch store validation ([3e140d1](https://github.com/groktopus/groktocrawl/commit/3e140d18bca42bc067d9c302d1bfda6b8c8b7a21))
* tighten integration test assertions for backward compat and schema conformance ([86aa81f](https://github.com/groktopus/groktocrawl/commit/86aa81f7131433e85003e6007350dfca4a5dc5d0))

## [Unreleased]

### Features

* scraper: add bounded autonomous CAPTCHA detection and recovery with typed unresolved errors

* agent: `--search-type` flag for research depth control — deep (multi-query, multi-pass, default) or focused (single-query, single-pass) ([#418](https://github.com/groktopus/groktocrawl/issues/418))
* agent: default research depth changed from auto-classified to `deep` — thorough multi-pass research is now the default for `groktocrawl agent`

### Models & API

* `AgentRequest.search_type`: `str` (default `"deep"`)
* `search_type` override: user preference now overrides auto-classification from Query Intelligence

### Fixes

* Gap detection: increased context window from 4000 to 12000 chars, improved prompt to detect implicit coverage gaps relative to the original query
* Research memory cache: deep requests now bypass cache (focused cached results don't satisfy deep requests)

## [0.11.0](https://github.com/groktopus/groktocrawl/compare/v0.10.1...v0.11.0) (2026-06-29)

> _The One That Sees_ — images across the entire stack: scrape, search, crawl, agent, and CLI.

### Highlights

**Scrape extracts structured image metadata.** When `formats` includes `"images"`, the scraper parses every `<img>` tag from the DOM before markdown conversion — capturing `src`, `alt`, `width`, `height`, and document-order position. Relative URLs are resolved, data URIs can be stripped, and the results land in `ScrapeData.images` alongside the markdown body. Default behavior is unchanged (no images extracted unless requested).

**Image search populates `data.images[]`.** `POST /v2/search` with `sources=["images"]` routes queries to SearXNG image engines and populates the `data.images` slot (previously always empty). A single query can return `data.web` and `data.images` simultaneously when combined sources are requested. The CLI adds `--search-type images` as a convenience shorthand.

**Crawl inherits image extraction.** `POST /v2/crawl` already forwards `scrape_options` (including `formats`) to every page scrape. The CLI now exposes `--format images` on the crawl subcommand so users can request image metadata on every crawled page.

**CLI image display and download.** Search output renders image results as a separate section with title, dimensions, and source URL. Scrape output shows an "Images found on page: N" summary with filename and dimensions. The new `--download-images` flag (scrape and crawl) saves images to `_images/` with concurrent HTTP downloads, content-type validation, HTTP status checking, and filename deduplication.

**Agent learns to gather images.** `--include-images` on the agent command plumbs through the entire research pipeline — from CLI flag → `AgentRequest.include_images` → worker → `run_research` → `_scrape_urls`, which passes `scrape_options={"formats":["markdown","images"]}` to the scraper. Agent-collected images arrive alongside the text synthesis.

### Features

* scrape: `formats=["images"]` extracts `<img>` metadata from DOM ([#370](https://github.com/groktopus/groktocrawl/issues/370))
* search: `data.images[]` populated from SearXNG image engines ([#371](https://github.com/groktopus/groktocrawl/issues/371))
* CLI: display image results in search and scrape output ([#372](https://github.com/groktopus/groktocrawl/issues/372))
* crawl: inherit `formats: images` from scrape via `scrape_options` passthrough ([#373](https://github.com/groktopus/groktocrawl/issues/373))
* CLI: `--download-images` flag for scrape and crawl ([#374](https://github.com/groktopus/groktocrawl/issues/374))
* agent: `--include-images` CLI flag for image-aware research ([#375](https://github.com/groktopus/groktocrawl/issues/375))

### Models & API

* `ImageData` model: `url`, `alt`, `width`, `height`, `position`
* `ImageSearchResult` model: `title`, `image_url`, `image_width`, `image_height`, `url`, `position`
* `ScrapeData.images`: `list[ImageData] | None`
* `AgentRequest.include_images`: `bool` (default `false`)
* `"images"` added to `VALID_SCRAPE_FORMATS`

## [0.10.1](https://github.com/groktopus/groktocrawl/compare/v0.10.0...v0.10.1) (2026-06-28)

> _The One That Fights Back_ — curl_cffi bypasses Akamai, Playwright stops waiting for Cloudflare challenges that never finish, and the CLI finally speaks API.

### Highlights

**Curl_cffi replaces httpx in the scraper stack.** The fetch tiers now use `curl_cffi.AsyncSession(impersonate="chrome131")` with BoringSSL-based TLS fingerprinting. Sites behind Akamai and Cloudflare edge that previously blocked at TLS handshake time are now reachable by Tiers 1-2. Bundles `libcurl-impersonate-chrome` in the Docker image.

**Tier 2 learned to handle HTML responses.** When a site doesn't return markdown via content negotiation but serves HTML, the scraper now converts it to markdown via readability+markdownify instead of dropping it. This closes a blind spot exposed by the curl_cffi swap: now that Tier 2 can reach Akamai-shielded pages, it was fetching HTML that it couldn't use.

**Cloudflare challenges no longer stall the pipeline.** The Playwright renderer was using `wait_until="networkidle"` which never completes on Cloudflare-protected pages — the JS challenge keeps the network busy indefinitely. The goto would time out at 45s, the scraper would raise, and the pipeline would skip straight to `SCRAPE_FAILED`. Now the scraper loads the challenge page in under a second (`domcontentloaded`), actively polls for the cooldown to clear (checking page title and `cf_clearance` cookie every 2s for up to 30s), and if the challenge persists, gracefully falls through to FlareSolverr instead of returning challenge-HTML garbage as if it were real content. Once through, it waits for the real page to settle before extracting.

**Barrier detection learned to recognize Cloudflare's newer challenge variants.** Pages that hit the "Verification successful. Waiting for turbo.az to respond" or "Enable JavaScript and cookies to continue" screens were previously invisible to the classifier — they passed the quality gate and were returned as "valid" content. Those phrases are now in `CLOUDFLARE_INDICATORS`, so the post-extraction barrier check catches them and routes them correctly.

**CLI now covers every `/v2/` endpoint.** Three new subcommands: `batch-scrape` (job status, cancel, paginated results), `monitor run` (manual trigger), and `parse-upload` (two-step large file upload). A new CI check (`check-cli-coverage.py`) enforces parity on every PR so future API endpoints won't land without CLI counterparts.

### Features

* CLI: `batch-scrape` subcommand with status, cancel, and errors ([#354](https://github.com/groktopus/groktocrawl/pull/354))
* API-CLI surface parity: ADR-0039, CI check, and PR template ([#357](https://github.com/groktopus/groktocrawl/pull/357))
* CLI: `monitor run` subcommand for manual trigger
* CLI: `parse-upload` subcommand for two-step file upload
* curl_cffi fetch client with Chrome 131 TLS fingerprint impersonation ([#363](https://github.com/groktopus/groktocrawl/pull/363))
* Tier 2 HTML-to-markdown fallback via readability+markdownify ([#368](https://github.com/groktopus/groktocrawl/pull/368))

### Bug Fixes

* fix: recover `_head_probe` crash from curl_cffi migration
* fix: active Cloudflare challenge polling with domcontentloaded goto strategy ([#369](https://github.com/groktopus/groktocrawl/pull/369), closes [#365](https://github.com/groktopus/groktocrawl/issues/365))

### CI & Infrastructure

* add check-cli-coverage.py to enforce API-CLI parity in CI

## [0.10.0](https://github.com/groktopus/groktocrawl/compare/v0.9.0...v0.10.0) (2026-06-27)

> _The One Where the Robot Gets Organized_ — monitors, batch jobs, file uploads, and a CI that doesn't melt.

### Highlights

**Monitors grew up.** You can now trigger a monitor check on demand (`POST /v2/monitor/:id/run`), browse check history (`GET /v2/monitor/:id/checks`), and query individual check results. The monitor system graduated from "fire and forget" to something you can actually debug.

**Batch scraping got real endpoints.** Status polling (`GET /v2/batch/scrape/:id` with pagination), cancellation, and a dedicated errors endpoint. The black box is now a glass box — you can watch a batch scrape unfold, page through results, and see exactly which URLs failed and why.

**Large file parsing, two ways.** The parse endpoint now supports a two-step upload flow: `PUT /v2/parse/upload/:id` to stage a file (up to 3 hours), then reference it via `upload_id` in the parse form. Atomic Lua-scripted get-and-delete means two concurrent parse jobs won't fight over the same upload. Direct multipart upload still works for small files.

**Concurrency, visible.** `GET /v2/concurrency-check` tells you how many jobs are actively processing versus the configured maximum. Useful for tuning before you hammer the API.

**CI stopped lying to us.** The test suite was passing (mostly) but the CI pipeline was a mess of timeouts, OOM kills, deptry tracebacks, and portal tests that hung against a real backend. v0.10.0 ships with Docker-native health checks, a persistent HF model cache volume that doesn't re-download 6.5GB of embeddings on every build, timeouts that actually fit the workload, and a portal test suite that tests the portal (not the agent stack).

### What's Next

The foundation is solid. v0.11.0 targets the observability layer: richer job lifecycle telemetry, structured error classification, and a Prometheus metrics pass across all services.

### Features

* add batch scrape status, cancel, and errors endpoints ([#344](https://github.com/groktopus/groktocrawl/pull/344))
* add monitor manual trigger endpoint (`POST /v2/monitor/:id/run`) ([#349](https://github.com/groktopus/groktocrawl/pull/349))
* add monitor check history endpoints ([#346](https://github.com/groktopus/groktocrawl/pull/346))
* add two-step parse upload endpoints for large files ([#347](https://github.com/groktopus/groktocrawl/pull/347))
* add concurrency check endpoint (`GET /v2/concurrency-check`) ([#348](https://github.com/groktopus/groktocrawl/pull/348))

### Bug Fixes

* update batch scrape tests to match current error-handling behavior ([4be3909](https://github.com/groktopus/groktocrawl/commit/4be3909))
* remove real-downstream tests from test_portal.py that hung against live agent-svc ([413207e](https://github.com/groktopus/groktocrawl/commit/413207e))
* correct deptry `--per-rule-ignores` format to stop emitting tracebacks on every CI run ([8034bf3](https://github.com/groktopus/groktocrawl/commit/8034bf3))

### CI & Infrastructure

* add Docker health checks and increase integration test timeouts ([#352](https://github.com/groktopus/groktocrawl/pull/352))
* serve embedding models from persistent Docker volume (hf-cache), removing 6.5GB model download from every build ([3d96a5d](https://github.com/groktopus/groktocrawl/commit/3d96a5d))


## [0.9.0](https://github.com/groktopus/groktocrawl/compare/v0.8.0...v0.9.0) (2026-06-24)


### Features

* add analytics counter pipeline with Valkey counters, Prometheus export, error tracking, and feature toggle observability ([63d4aa7](https://github.com/groktopus/groktocrawl/commit/63d4aa76f39ba9686f42db93cd9730b1cdc00573))
* add browser-svc unit and integration tests covering session management, stealth, cookies, and all 8 browser actions ([857f076](https://github.com/groktopus/groktocrawl/commit/857f076006698ea4ecbf0c83b038efb48cf6372c))
* add env-var-based feature toggle system in common/features.py ([bb0c9a6](https://github.com/groktopus/groktocrawl/commit/bb0c9a6f54b8b4999558457bda18f771b8fe004d))
* add gitleaks secret scanning CI job to workflow ([7ff312f](https://github.com/groktopus/groktocrawl/commit/7ff312f956e1e6fce758a8aa12d2b5ee699bf3d2))
* add Grafana dashboard JSONs for agent-svc and scraper-svc ([5aec055](https://github.com/groktopus/groktocrawl/commit/5aec055c135ac73754719a7f5c25ad4694579ca4))
* add mypy type checking job to CI workflow ([34cb786](https://github.com/groktopus/groktocrawl/commit/34cb7863596b50dd237fdaf7a05e4279e3151675))
* add parse-svc unit and integration tests covering all 8 parsers ([59929b3](https://github.com/groktopus/groktocrawl/commit/59929b364c1253583404a005e296ebd03a37bb64))
* add pip-audit CI job and update integration tests to copy full tests/ directory ([627ddcd](https://github.com/groktopus/groktocrawl/commit/627ddcd452ef60ed4242c031cafd2e999a85ab71))
* add portal-svc unit and integration tests covering proxy logic and SSE streaming ([09edbf6](https://github.com/groktopus/groktocrawl/commit/09edbf60d9a8e117b27e1150045e5d0826dbb134))
* add Prometheus alerting rules and per-alert runbooks ([5a3d1d2](https://github.com/groktopus/groktocrawl/commit/5a3d1d23581e2932feed2d350828f5903cae1196))
* add reusable CircuitBreaker class and apply to portal-svc proxy ([14087b3](https://github.com/groktopus/groktocrawl/commit/14087b3cd31fc3c5c1a1fe27eb2ee7b5a5e9677a))
* add ruff TD enforcement job to CI workflow ([9c75fae](https://github.com/groktopus/groktocrawl/commit/9c75fae39872c65c8590f21825a792c13f36fcaf))
* add semantic-svc unit and integration tests covering 9 unit areas and 20+ integration scenarios ([4c13f22](https://github.com/groktopus/groktocrawl/commit/4c13f22b9a57dc9a091d39805c1ff5004c582818))
* add SensitiveDataFilter to common/logging.py and standardize /metrics media type ([489b8f8](https://github.com/groktopus/groktocrawl/commit/489b8f89fa08a10daafe2013277a2b81791a19d8))
* add structured logging, request-ID tracing, and /metrics endpoint to llm-svc ([9f0a437](https://github.com/groktopus/groktocrawl/commit/9f0a4379223bc0359adaf7354b8613023dc4f385))
* add structured logging, request-ID tracing, and /metrics endpoint to search-svc ([c160c5b](https://github.com/groktopus/groktocrawl/commit/c160c5b5332407ad05a2736ca4bdee726fae8211))
* **content-dedup:** add DedupManager for multi-layer content deduplication ([bcb938e](https://github.com/groktopus/groktocrawl/commit/bcb938edbd4d648330d737deff12b7eb1363be1e))
* **content-dedup:** add DedupManager for multi-layer content deduplication ([55858a4](https://github.com/groktopus/groktocrawl/commit/55858a4a2a654140de5b50cf34a7a898147fdd85))
* **crawl-active-endpoint:** implement GET /v2/crawl/active endpoint ([7e9fd35](https://github.com/groktopus/groktocrawl/commit/7e9fd35121d6e89fdf5073c36f4d513c6edc1f80))
* **crawl-active-endpoint:** implement GET /v2/crawl/active endpoint ([0be9ca4](https://github.com/groktopus/groktocrawl/commit/0be9ca428e18e3e53e062e56ce922c8c5a017d2e))
* **crawl-advanced-scrape-options:** extend ScrapeOptions with actions, location, proxy, blockAds, parsers ([b45ad71](https://github.com/groktopus/groktocrawl/commit/b45ad7155c336fab593bf9c12e4183fb38aed97b))
* **crawl-advanced-scrape-options:** extend ScrapeOptions with actions, location, proxy, blockAds, parsers ([e959e06](https://github.com/groktopus/groktocrawl/commit/e959e063b4d7a88782d1ae4b7f4e8699edb89b96))
* **crawl-cache:** add Valkey-backed CrawlCache with maxAge/minAge semantics ([d3ae54f](https://github.com/groktopus/groktocrawl/commit/d3ae54f73ac3cb4653db6623c44af4447249867c))
* **crawl-cache:** Valkey-backed CrawlCache with maxAge/minAge semantics ([59fc7dc](https://github.com/groktopus/groktocrawl/commit/59fc7dc9a16148a7885fdefcbf861859dbaec53a))
* **crawl-cli-update:** add --max-pages, --ignore-query-params flags and improve error handling ([2c31641](https://github.com/groktopus/groktocrawl/commit/2c3164140ffda1aaae135f90553423ef9d2ff338))
* **crawl-cli-update:** add --max-pages, --ignore-query-params flags and improve server-unreachable error handling ([31c9ad5](https://github.com/groktopus/groktocrawl/commit/31c9ad5b370f2aa9027b710db5529b540b0d223c))
* **crawl-concurrency:** implement maxConcurrency and delay in CrawlEngine ([d282c18](https://github.com/groktopus/groktocrawl/commit/d282c189a27c658b15cb9caab009c6d7f42a60b1))
* **crawl-concurrent-progress-and-errors:** ensure atomic progress, unique webhook IDs, distinguished errors, timeout handling, task tracker usage ([7372030](https://github.com/groktopus/groktocrawl/commit/7372030abf29510294c4d80a10bc942e9d2cefb1))
* **crawl-engine-core:** implement CrawlEngine with BFS crawl loop ([e0032be](https://github.com/groktopus/groktocrawl/commit/e0032beff2725a2199c8e0aec0c80b159adb6d57))
* **crawl-engine-core:** implement CrawlEngine with BFS crawl loop ([38e93e3](https://github.com/groktopus/groktocrawl/commit/38e93e3661169f22f5ad7a47d8a11a6dc7ea0f8e))
* **crawl-errors:** implement GET /v2/crawl/{id}/errors endpoint with error type classification ([658dddb](https://github.com/groktopus/groktocrawl/commit/658dddb648d57ca7d4a07e23eb71bc4f255f39a3))
* **crawl-integration-tests:** add comprehensive integration tests for crawl features ([527a446](https://github.com/groktopus/groktocrawl/commit/527a4466c6d4e7e3baaa5e51df69b86299752f46))
* **crawl-job-lifecycle:** implement full crawl lifecycle with cancellation, webhooks, timeouts, and activity feed ([2a71a92](https://github.com/groktopus/groktocrawl/commit/2a71a925684e1814b331d49aad9a1c4fc27920b6))
* **crawl-metrics:** add Prometheus metrics for crawl operations ([59ade0c](https://github.com/groktopus/groktocrawl/commit/59ade0c48e9d53a638d8536f3518949654e028bb))
* **crawl-metrics:** add Prometheus metrics for crawl operations ([f1a744e](https://github.com/groktopus/groktocrawl/commit/f1a744eae7cfffb39134a21511c1f7869bf099d5))
* **crawl-per-page-webhooks:** per-page webhook delivery with UUID webhookId, metadata echo, and HMAC signature ([8ab4c72](https://github.com/groktopus/groktocrawl/commit/8ab4c7236cac789c2a28663a41954aec5fb2cb16))
* **crawl-per-page-webhooks:** per-page webhook delivery with UUID webhookId, metadata echo, and HMAC signature ([f762b3e](https://github.com/groktopus/groktocrawl/commit/f762b3ece02d7a246000e7c573ec4a4670e96c53))
* **crawl-politeness-integration:** integrate scraper-svc politeness system into crawl worker ([6f5c8b5](https://github.com/groktopus/groktocrawl/commit/6f5c8b54ce3630591a62c99b991909988e3f5bc3))
* **crawl-politeness-integration:** integrate scraper-svc politeness system into crawl worker ([dae8179](https://github.com/groktopus/groktocrawl/commit/dae81795088296d310912a391c03d91b78a36f6e))
* **crawl-response-shape-parity:** add next pagination field, enhanced per-page metadata, creditsUsed, and camelCase aliases to CrawlStatusResponse ([ca0d916](https://github.com/groktopus/groktocrawl/commit/ca0d9161bc7939bd22c9b791dd356737a53599c9))
* **crawl-sse-streaming:** implement SSE streaming for crawl progress via stream:true on CrawlRequest ([6fd5172](https://github.com/groktopus/groktocrawl/commit/6fd517212943b6b41f899cbc701dfc90a4e91931))
* **crawl-sse-streaming:** SSE streaming for crawl progress ([296bf32](https://github.com/groktopus/groktocrawl/commit/296bf32393ea266987f742f8ab3ddf5cec08be7d))
* **crawl-status-response-enhancement:** enhance CrawlStatusResponse with timestamps, per-page metadata, and field validation ([9ff2c7f](https://github.com/groktopus/groktocrawl/commit/9ff2c7fb05d7852fcbf8f48ce2964ba134ae5098))
* **crawl-status-response-enhancement:** enhance CrawlStatusResponse with timestamps, per-page metadata, and field validation ([a6b677e](https://github.com/groktopus/groktocrawl/commit/a6b677eff5f518252e9b407ae71de6bd347b27ac))
* **domain-scope-controls:** implement crawlEntireDomain, allowSubdomains, allowExternalLinks scope controls ([d2eec4b](https://github.com/groktopus/groktocrawl/commit/d2eec4baca8f87cb655a3c5c8449b7f4ac527110))
* **domain-scope-controls:** implement crawlEntireDomain, allowSubdomains, allowExternalLinks scope controls ([987b2ec](https://github.com/groktopus/groktocrawl/commit/987b2ec72d5a7ecf369a1860ea353b82304eea13))
* **fix-sse-error-events:** add error_callback to CrawlEngine and fix SSE error event format ([4fca389](https://github.com/groktopus/groktocrawl/commit/4fca38902e4a36a24dfa589f64aa7deb5d5f8d15))
* **fix-sse-error-events:** add error_callback to CrawlEngine and fix SSE error event format ([3c7f5c7](https://github.com/groktopus/groktocrawl/commit/3c7f5c710a02ddcb690fb0ba81cfb1969c19d932))
* **nl-to-params:** implement prompt field on CrawlRequest and POST /v2/crawl/params-preview endpoint ([51db214](https://github.com/groktopus/groktocrawl/commit/51db214b8654710b6d1b689a42875eb3fe34baa1))
* **nl-to-params:** implement prompt field on CrawlRequest and POST /v2/crawl/params-preview endpoint ([28303bb](https://github.com/groktopus/groktocrawl/commit/28303bb4c55f0eada023c827ddc5f04bf3e2a1aa))
* **path-filtering:** implement includePaths/excludePaths with glob and regex support ([55d7e26](https://github.com/groktopus/groktocrawl/commit/55d7e26895905e2034672448cc9bb47c0d97e7aa))
* **path-filtering:** implement includePaths/excludePaths with glob and regex support ([4e74483](https://github.com/groktopus/groktocrawl/commit/4e74483f971ec5255b4415defdf533447cc071e6))
* POST /v2/enrich — list enrichment pipeline ([#329](https://github.com/groktopus/groktocrawl/issues/329)) ([#336](https://github.com/groktopus/groktocrawl/issues/336)) ([4b0535f](https://github.com/groktopus/groktocrawl/commit/4b0535f51b97987e9079722533471d8ee36e8662))
* POST /v2/find-similar — find semantically similar pages by URL ([#325](https://github.com/groktopus/groktocrawl/issues/325)) ([#332](https://github.com/groktopus/groktocrawl/issues/332)) ([75a9fb4](https://github.com/groktopus/groktocrawl/commit/75a9fb4cabcca2f0793c252f28a55b33e7e1b9c5))
* replace agent-svc inline logging/middleware/metrics with common/ imports ([8a1f6c4](https://github.com/groktopus/groktocrawl/commit/8a1f6c4006b32ec690dfce0c4d17fb343cff400c))
* richer content extraction on /v2/search and /v2/scrape ([#328](https://github.com/groktopus/groktocrawl/issues/328)) ([#331](https://github.com/groktopus/groktocrawl/issues/331)) ([632c612](https://github.com/groktopus/groktocrawl/commit/632c6126fb84fedcef1d8c358d74faffd6879418))
* **scrape-options-model:** add ScrapeOptions Pydantic model with full Firecrawl-compatible fields ([2803776](https://github.com/groktopus/groktocrawl/commit/280377630698b3a42b4b43296b1a8b6129a71361))
* search_type=deep — multi-pass agentic search on /v2/search ([#327](https://github.com/groktopus/groktocrawl/issues/327)) ([#335](https://github.com/groktopus/groktocrawl/issues/335)) ([206edc1](https://github.com/groktopus/groktocrawl/commit/206edc1f43818b05fdaff82db728993db936cc71))
* search-based monitors on /v2/monitor ([#326](https://github.com/groktopus/groktocrawl/issues/326)) ([#333](https://github.com/groktopus/groktocrawl/issues/333)) ([3a91e43](https://github.com/groktopus/groktocrawl/commit/3a91e43c6a2df53eb45ef577bf567ad2bdbbbe73))
* **shared-link-extractor:** create shared LinkExtractor module with extract_links(), filter_links(), and classify_links() ([517509f](https://github.com/groktopus/groktocrawl/commit/517509fb677bee116b1ee353827e7d99a0dde376))
* **sitemap-parser:** add SitemapParser with three-mode sitemap support ([ac5b0b6](https://github.com/groktopus/groktocrawl/commit/ac5b0b62981c277b3c81d1acba88c38a64ba781b))
* **sitemap-parser:** add SitemapParser with three-mode sitemap support (include/skip/only) ([1b88a0d](https://github.com/groktopus/groktocrawl/commit/1b88a0d22f73b86cdf516d08226b8e96026ea377))
* streaming search results — SSE on /v2/search ([#330](https://github.com/groktopus/groktocrawl/issues/330)) ([#334](https://github.com/groktopus/groktocrawl/issues/334)) ([58eca49](https://github.com/groktopus/groktocrawl/commit/58eca4965b8f17fc032688abf6996b6d02e6ecf0))
* **test-site-fixture-expansion:** expand test-site with crawl/scope testing endpoints ([40c22c6](https://github.com/groktopus/groktocrawl/commit/40c22c61ef038bdc3581119c90c8f0646fe7bb16))


### Bug Fixes

* add --redact to gitleaks CI to prevent secret exposure in logs ([cd20b56](https://github.com/groktopus/groktocrawl/commit/cd20b56fb822a49fe3230a084ddee2444fb5b12a))
* add .gitleaksignore for pre-existing README placeholder secret ([b6f6112](https://github.com/groktopus/groktocrawl/commit/b6f611281e9e573a8b0c07106fb3bc788a3a6ed3))
* add test-site health check to CI workflow ([#288](https://github.com/groktopus/groktocrawl/issues/288)) ([dd3e06d](https://github.com/groktopus/groktocrawl/commit/dd3e06dceb5fffb0a1c96e831880c40f60a6eecb)), closes [#287](https://github.com/groktopus/groktocrawl/issues/287)
* **cancel-race:** add status guard to complete_job() and fail_job() in store.py ([440a892](https://github.com/groktopus/groktocrawl/commit/440a892ae9639cee9b1dbf32693c679d9e54c3e1))
* **cancel-race:** add status guard to complete_job() and fail_job() in store.py ([440a892](https://github.com/groktopus/groktocrawl/commit/440a892ae9639cee9b1dbf32693c679d9e54c3e1))
* **cancel-race:** add status guard to complete_job() and fail_job() in store.py ([b051f7a](https://github.com/groktopus/groktocrawl/commit/b051f7a531ccea73441ae606521178bdca288da2))
* **ci:** copy agent-svc/agent source into test container ([bb1eb2f](https://github.com/groktopus/groktocrawl/commit/bb1eb2f7af5ebcd34afb470ed17143d35d656bd6))
* **ci:** fix integration test health check port (8000 → 8005) ([f7d92b5](https://github.com/groktopus/groktocrawl/commit/f7d92b5df6a4757156dcd7b3c4dd1a53ca98bea4))
* **ci:** fix integration test health check port (8000 → 8005) ([b313835](https://github.com/groktopus/groktocrawl/commit/b3138356a4ba834e91fb7f4d328ffe5bc82f448d))
* **ci:** fix integration test health check port and skip service unit tests ([e0032be](https://github.com/groktopus/groktocrawl/commit/e0032beff2725a2199c8e0aec0c80b159adb6d57))
* **ci:** include agent-svc in service copy/install loop ([85537da](https://github.com/groktopus/groktocrawl/commit/85537dad38a12895ce0c65b6b0c5549ef45a39a9))
* **ci:** include agent-svc in service copy/install loop ([f15c800](https://github.com/groktopus/groktocrawl/commit/f15c8008d39d935279e876ef209217a7f3d82bec))
* **ci:** install all service packages in agent-svc for integration tests ([947b5df](https://github.com/groktopus/groktocrawl/commit/947b5df2360b46a7db8b0762a6688747559aa477))
* **ci:** install all service packages in agent-svc for integration tests ([944dd63](https://github.com/groktopus/groktocrawl/commit/944dd6327ac43c40a1cdb29ab89d45d93c15fd86))
* **ci:** install jinja2 for portal tests and fix gitleaks ignore line ([c409ae5](https://github.com/groktopus/groktocrawl/commit/c409ae5d7191a516f2ca4ec77daa90ad232603a5))
* **ci:** install jinja2 for portal tests and fix gitleaks ignore line ([f5e7ec8](https://github.com/groktopus/groktocrawl/commit/f5e7ec803dd5dafa776aae0da195c1baede8edbe))
* **ci:** install playwright in agent-svc for browser unit tests ([c25aa30](https://github.com/groktopus/groktocrawl/commit/c25aa30611079b5fd16d377198fa436687d2f171))
* **ci:** install playwright in agent-svc for browser unit tests ([98a31f6](https://github.com/groktopus/groktocrawl/commit/98a31f617008eee83d6b3cbfa89da1d1d298e164))
* **ci:** skip service unit tests in integration test job ([060f5be](https://github.com/groktopus/groktocrawl/commit/060f5be8b3f5366e572689a60c0232f5fd983835))
* **ci:** skip service unit tests in integration test job ([0e77a0b](https://github.com/groktopus/groktocrawl/commit/0e77a0be66f909ab8f3789e3e1443b580f02b625))
* **ci:** update gitleaksignore line number for curl-auth-header (333 → 346) ([848ec13](https://github.com/groktopus/groktocrawl/commit/848ec137812c7e31308f2c89c1dd2c49fa81bd35))
* **cli-crawl-camelcase-fix:** fix CLI/API naming mismatch for crawl flags ([05eeaf2](https://github.com/groktopus/groktocrawl/commit/05eeaf22e07455e20b707c0ec6659aec0da890b9))
* correct gitleaks download URL from linux_amd64 to linux_x64 ([8b82a13](https://github.com/groktopus/groktocrawl/commit/8b82a13fc638a1803b54737ec5136398ebc63621))
* **crawl-cache:** combined maxAge+minAge semantics returns stale data when cache is older than maxAge ([de67cad](https://github.com/groktopus/groktocrawl/commit/de67cad8fd9dadfcdb50de54de2082e6b48b7454))
* **crawl-cache:** combined maxAge+minAge semantics returns stale data when cache is older than maxAge ([13e5f14](https://github.com/groktopus/groktocrawl/commit/13e5f141b680105223c9f6c0882f90f148c6a0cb))
* **crawl-engine-core:** use direct Redis set for store progress updates ([8a7d22f](https://github.com/groktopus/groktocrawl/commit/8a7d22f4aabff628ac8e55a86b408dd122b08e7d))
* **docs:** sync ADR-0038, README, AGENTS.md, CHANGELOG, test port with implementation ([c054dd3](https://github.com/groktopus/groktocrawl/commit/c054dd33973943689b2251219a45faeeb40e8d76))
* don't abort crawl on empty sitemap pages — retry, skip, continue ([ad97eeb](https://github.com/groktopus/groktocrawl/commit/ad97eeb536bb3c44bfbf1b39a6f91a3b256c7754))
* don't abort crawl on empty sitemap pages — retry, skip, continue ([17919b4](https://github.com/groktopus/groktocrawl/commit/17919b458de7d68ba2cd789b80ae7c8e5eec13c7)), closes [#314](https://github.com/groktopus/groktocrawl/issues/314)
* harden semantic-svc migration endpoints with API key auth and cached target model ([f1c3ae1](https://github.com/groktopus/groktocrawl/commit/f1c3ae1ee36e3e0e3890430c1e1bd905ece55cd0))
* **lint:** auto-fix pre-existing ruff lint issues during scrutiny validation ([9f5d516](https://github.com/groktopus/groktocrawl/commit/9f5d51603139debfcb52c7f0a1a2537bf8abb579))
* move analytics exporter background task to startup event handler ([27131d3](https://github.com/groktopus/groktocrawl/commit/27131d3e3dad5ab9282f50f9a8070d73a7f10f0f))
* outer while loop also needs max_pages &lt;= 0 guard ([34620c6](https://github.com/groktopus/groktocrawl/commit/34620c636c5ea8ffcf55075a5d32a6e55f9639c8))
* remove non-existent semantic-svc/semantic from docker.yml copy loop ([ab5d0f7](https://github.com/groktopus/groktocrawl/commit/ab5d0f71d6f44697c4f46f2c091f80f3da0e5e21))
* remove unused webhook_id_key parameter from deliver_webhook() ([be784c6](https://github.com/groktopus/groktocrawl/commit/be784c65416d9699ed1d733280617e43e20f071b))
* replace gitleaks-action with direct gitleaks install in CI ([7e460c1](https://github.com/groktopus/groktocrawl/commit/7e460c16d30ae553dd5a219b9011d73134f038fa))
* resolve all 49 pre-existing mypy type errors across 7 files ([6796b06](https://github.com/groktopus/groktocrawl/commit/6796b06b735fa8d97ae40b630d08ce7530491d80))
* resolve pre-existing test failures and lint issues for observability milestone validation ([a103920](https://github.com/groktopus/groktocrawl/commit/a103920ad5d959af5646ee3ca804308db8ba20a1))
* retry dedup — unmark URL from _seen before re-queuing; clear error on retry ([cb6cdfb](https://github.com/groktopus/groktocrawl/commit/cb6cdfbefedfb7bbce8a3a2298e82c910db93ec9))
* **robots-user-agent:** pass robots_user_agent through politeness check layer ([a4e713c](https://github.com/groktopus/groktocrawl/commit/a4e713cfffc2bcadf949d36c98d340b6a7ae5471))
* **scrutiny:** resolve pre-existing lint and typecheck blockers for concurrency milestone ([ddb2051](https://github.com/groktopus/groktocrawl/commit/ddb2051666b1ea6962c598c1f8053aac55a7f86f))
* **sse-crawl:** call store.complete_job()/fail_job() after SSE stream ends ([fe99526](https://github.com/groktopus/groktocrawl/commit/fe995264a739ecff49eb28e223a8677a85f4216e))
* **sse-crawl:** call store.complete_job()/fail_job() after SSE stream ends ([fe99526](https://github.com/groktopus/groktocrawl/commit/fe995264a739ecff49eb28e223a8677a85f4216e))
* **sse-crawl:** call store.complete_job()/fail_job() after SSE stream ends ([42fdbe9](https://github.com/groktopus/groktocrawl/commit/42fdbe92899364518b3966db01db4dde1e24c67c))
* **tests:** align CLI test expectations with snake_case crawl params ([e0bee6e](https://github.com/groktopus/groktocrawl/commit/e0bee6e73fa036577100cf5366e722923a67ac20))
* Tier 1 (llms.txt) should only fire for root URLs ([a7feb28](https://github.com/groktopus/groktocrawl/commit/a7feb2810f71ac9366c45b70ad787e60ce7d3289))
* Tier 1 (llms.txt) should only fire for root URLs ([8ce07cb](https://github.com/groktopus/groktocrawl/commit/8ce07cb9ee493e9bcb039a7ab0c0b3961b7eab33)), closes [#316](https://github.com/groktopus/groktocrawl/issues/316)
* unlimited crawl defaults, sitemap low-value filter, gzip parsing fix ([536c73a](https://github.com/groktopus/groktocrawl/commit/536c73aab7d66151caf73856900522482d86637f))
* unlimited crawl defaults, sitemap low-value filter, gzip parsing fix ([2c3bf2a](https://github.com/groktopus/groktocrawl/commit/2c3bf2a656b0fed109e80078165cd92bfb8135d4))
* use &gt;= instead of &gt; for SessionData.expired boundary check ([25d5126](https://github.com/groktopus/groktocrawl/commit/25d51266254c8adffcff6dfb95e42b7120685d96))


### Documentation

* **crawl-adr:** add ADR-0038 documenting crawl engine architecture ([190b16a](https://github.com/groktopus/groktocrawl/commit/190b16a93fd65d7f1917d6e5cf74c11852840148))
* **crawl-docs-update:** update documentation artifacts for crawl feature parity ([301ff2c](https://github.com/groktopus/groktocrawl/commit/301ff2c6361777a991c0a9f7a9e88a72d1949908))

## [0.8.0](https://github.com/groktopus/groktocrawl/compare/v0.7.0...v0.8.0) (2026-06-18)


### Features

* 10 security scraper adapters — threat intelligence API coverage ([c37637e](https://github.com/groktopus/groktocrawl/commit/c37637eb7d8b518485d4159d26e7ac9ccc15dd3d))
* add .dockerignore to reduce Docker build context size ([#254](https://github.com/groktopus/groktocrawl/issues/254)) ([eb48825](https://github.com/groktopus/groktocrawl/commit/eb48825497f5a7a428fc326cfb9b39b341512603)), closes [#241](https://github.com/groktopus/groktocrawl/issues/241)
* add 10 security scraper adapters for threat intelligence APIs ([4330b16](https://github.com/groktopus/groktocrawl/commit/4330b166ddbbbefca857bb40b2087cd18ee95cd0))
* add DEBUG-level logging to scraper adapter fallback chains ([#251](https://github.com/groktopus/groktocrawl/issues/251)) ([f12706d](https://github.com/groktopus/groktocrawl/commit/f12706dacd47096cbf1bab6d905f14d58418f6b8)), closes [#239](https://github.com/groktopus/groktocrawl/issues/239)
* add DNS resolution failure logging to SSRF guard ([#253](https://github.com/groktopus/groktocrawl/issues/253)) ([080ef60](https://github.com/groktopus/groktocrawl/commit/080ef603a5f2ec3205662d397f080a9c8a16e4e1))
* add graceful shutdown handling for fire-and-forget async tasks ([#231](https://github.com/groktopus/groktocrawl/issues/231)) ([360b526](https://github.com/groktopus/groktocrawl/commit/360b52616055295da5e042bbf920ae8320cc6272))
* add Greenhouse and AshbyHQ ATS adapters ([#216](https://github.com/groktopus/groktocrawl/issues/216)) ([777fbd3](https://github.com/groktopus/groktocrawl/commit/777fbd387e8d20b2ccbc47b12fa4639d2f3765b7))
* add lifespan-based model loading and startup readiness to semantic-svc ([#223](https://github.com/groktopus/groktocrawl/issues/223)) ([5b98caf](https://github.com/groktopus/groktocrawl/commit/5b98caff097ea9cbbd25d4712b7923759d21bfc6))
* add portal-svc health probe to agent-svc ([#218](https://github.com/groktopus/groktocrawl/issues/218)) ([429335c](https://github.com/groktopus/groktocrawl/commit/429335ceffa007bdeb3672a9d3109aa97a55747c))
* add Query Intelligence — LLM-powered research planning (Phase 0) ([865da0d](https://github.com/groktopus/groktocrawl/commit/865da0db27906ce43d7b3e83c241f05a9ae7c42c))
* add search volume controls to agent-svc ([#214](https://github.com/groktopus/groktocrawl/issues/214)) ([064628d](https://github.com/groktopus/groktocrawl/commit/064628d41b24aba9ca3f8874dbe8ef08e85eb293))
* add Vaco (Highspring) adapter for jobs.vaco.com ([#222](https://github.com/groktopus/groktocrawl/issues/222)) ([8a5b54e](https://github.com/groktopus/groktocrawl/commit/8a5b54ecd3eb47fd20847a29437784266e171809))
* agent readiness level-up — observability, type checking, CI tooling, tests ([#267](https://github.com/groktopus/groktocrawl/issues/267)) ([3dbdd82](https://github.com/groktopus/groktocrawl/commit/3dbdd82f5ee4711c2005212345a96308b60976d3))
* NVD and CVE Program adapters — structured CVE data extraction ([b327921](https://github.com/groktopus/groktocrawl/commit/b32792105c2128c36bc352b111726b14da759a66))
* NVD and CVE Program adapters for structured CVE data extraction ([4e10527](https://github.com/groktopus/groktocrawl/commit/4e10527842984d48d189a33ce0c2cf308e21d9c1))
* pre-tier HEAD probe to detect bot protection before scrape pipeline ([#276](https://github.com/groktopus/groktocrawl/issues/276)) ([bce7edc](https://github.com/groktopus/groktocrawl/commit/bce7edcc7ae29cc2bf7064fe30b921f1368d73d9)), closes [#272](https://github.com/groktopus/groktocrawl/issues/272)
* Project Gutenberg adapter — chapter-structured book extraction ([#182](https://github.com/groktopus/groktocrawl/issues/182)) ([94a32d0](https://github.com/groktopus/groktocrawl/commit/94a32d0ca23e41b8fb6982081604e29006d9fd19)), closes [#181](https://github.com/groktopus/groktocrawl/issues/181)
* Query Intelligence — LLM-powered research planning (Phase 0) ([e7f87da](https://github.com/groktopus/groktocrawl/commit/e7f87da5b956cde382df66ef27bac79b89ead131))
* replace SearXNG with SlopSearX in Docker stack ([#204](https://github.com/groktopus/groktocrawl/issues/204)) ([d384355](https://github.com/groktopus/groktocrawl/commit/d3843553916ccdadf5c1065b0c8d585f1927bb50))
* Shopify adapter — bypass UCP content-negotiation trap ([04df892](https://github.com/groktopus/groktocrawl/commit/04df892cceef4280f032262ceb84b7f4bb1eec6f))
* Shopify adapter — bypass UCP content-negotiation trap ([f7852cf](https://github.com/groktopus/groktocrawl/commit/f7852cf206aa2a7e4042e625fd89e8797c35645e))
* standardize error handling across all API endpoints ([#208](https://github.com/groktopus/groktocrawl/issues/208)) ([5b1154d](https://github.com/groktopus/groktocrawl/commit/5b1154d3e2a607979f48a81f1f36f93abde2a007)), closes [#185](https://github.com/groktopus/groktocrawl/issues/185)


### Bug Fixes

* add from __future__ import annotations for Python 3.9 compat ([c8b8477](https://github.com/groktopus/groktocrawl/commit/c8b8477c85da6e4bfea1fb2be108cf553021c51c))
* add from __future__ import annotations for Python 3.9 compat ([a3cf2a8](https://github.com/groktopus/groktocrawl/commit/a3cf2a86d787161bcfe5e7191802cec7e6622306))
* add LLM health check and SSE status heartbeat to agent endpoint ([#265](https://github.com/groktopus/groktocrawl/issues/265)) ([e3df9b3](https://github.com/groktopus/groktocrawl/commit/e3df9b3ee3752d7e3176971fae3eb8531e301121))
* add missing Qdrant lookup logging to batch index endpoint ([#257](https://github.com/groktopus/groktocrawl/issues/257)) ([209f964](https://github.com/groktopus/groktocrawl/commit/209f9648d5b731ac73f113f9256a21aded1e253d))
* allow dependabot PRs to pass CI ([#230](https://github.com/groktopus/groktocrawl/issues/230)) ([ec4ab1d](https://github.com/groktopus/groktocrawl/commit/ec4ab1d52b9f4f9d66f084dbbd0c023d50ad3e62))
* correct SlopSearX image tag — CI strips v prefix ([#205](https://github.com/groktopus/groktocrawl/issues/205)) ([3691023](https://github.com/groktopus/groktocrawl/commit/3691023a19a10de9257f2edf9c56c3ac654209e5))
* deduplicate SSRF guard across services ([#235](https://github.com/groktopus/groktocrawl/issues/235)) ([dfa36fd](https://github.com/groktopus/groktocrawl/commit/dfa36fd1fbcd9f57b1190f60272cc3e491488835))
* deduplicate SSRF guard across services, consolidate on common.url.is_private_host ([dfa36fd](https://github.com/groktopus/groktocrawl/commit/dfa36fd1fbcd9f57b1190f60272cc3e491488835))
* deduplicate SSRF guard across services, consolidate on common.url.is_private_host ([cab348f](https://github.com/groktopus/groktocrawl/commit/cab348f6ec19f43dd516c8c43b01c80f555958c2)), closes [#195](https://github.com/groktopus/groktocrawl/issues/195)
* deprioritize video platform URLs in agent research pipeline ([#184](https://github.com/groktopus/groktocrawl/issues/184)) ([669c51b](https://github.com/groktopus/groktocrawl/commit/669c51b984a4e4965314751a45d35d4d01fee338))
* enable rich search for vector and hybrid_vector retrieval modes ([#234](https://github.com/groktopus/groktocrawl/issues/234)) ([43a8a16](https://github.com/groktopus/groktocrawl/commit/43a8a16ce44e8270c4f24e54d5fae12030694512))
* handle read-only settings.yml mount in searxng entrypoint ([40495f9](https://github.com/groktopus/groktocrawl/commit/40495f935d8f9df589f95639b60bade8055ff035))
* harden Playwright stealth configuration for anti-bot bypass ([#277](https://github.com/groktopus/groktocrawl/issues/277)) ([2794051](https://github.com/groktopus/groktocrawl/commit/279405170dea61caad5e26164b7656a63b2314af)), closes [#273](https://github.com/groktopus/groktocrawl/issues/273)
* increase agent prompt max_length from 10000 to 100000 ([#264](https://github.com/groktopus/groktocrawl/issues/264)) ([d088574](https://github.com/groktopus/groktocrawl/commit/d08857455ecbf6b40e1ec73c237f041b0577c19f))
* install system deps for Playwright Chromium Dockerfiles ([#259](https://github.com/groktopus/groktocrawl/issues/259)) ([d03995e](https://github.com/groktopus/groktocrawl/commit/d03995e994de8191b1370d8d9c71307323977261))
* lift FlareSolverr tier out of if result: gating ([#275](https://github.com/groktopus/groktocrawl/issues/275)) ([d74aac6](https://github.com/groktopus/groktocrawl/commit/d74aac647c39215936a0ddad933a9385c5f85bb6)), closes [#274](https://github.com/groktopus/groktocrawl/issues/274)
* log Qdrant lookup failures instead of silently swallowing them ([#248](https://github.com/groktopus/groktocrawl/issues/248)) ([f862b45](https://github.com/groktopus/groktocrawl/commit/f862b45a18f6d51b10eefbeb24bce4e2ae7a5c89)), closes [#237](https://github.com/groktopus/groktocrawl/issues/237)
* log semantic indexing failures instead of silently swallowing them ([#249](https://github.com/groktopus/groktocrawl/issues/249)) ([44b166a](https://github.com/groktopus/groktocrawl/commit/44b166a58e6b88294c80ea6b22d10f706dc829f8)), closes [#238](https://github.com/groktopus/groktocrawl/issues/238)
* narrow exception handling in semantic-svc model loading ([#252](https://github.com/groktopus/groktocrawl/issues/252)) ([28d0e05](https://github.com/groktopus/groktocrawl/commit/28d0e05c0e98b7686ed0c39a498dd6ff11972af1)), closes [#243](https://github.com/groktopus/groktocrawl/issues/243)
* only send enable_thinking param when explicitly opted in ([c97170d](https://github.com/groktopus/groktocrawl/commit/c97170d94d595b0afe220af5ba5b4ad72d2a0025))
* pass HF_TOKEN as Docker build arg for model downloads ([43ad4e0](https://github.com/groktopus/groktocrawl/commit/43ad4e068b143a4007abd19eac8954a95a638827))
* pin Qdrant image and cap memory to prevent OOM ([483f148](https://github.com/groktopus/groktocrawl/commit/483f148d004355d4f96e164e36dfb773ce3e63f7))
* print agent errors to stdout instead of stderr ([#266](https://github.com/groktopus/groktocrawl/issues/266)) ([198a41d](https://github.com/groktopus/groktocrawl/commit/198a41dcc414c4812786da1172a7bbf59c6a07da)), closes [#263](https://github.com/groktopus/groktocrawl/issues/263)
* read API key from API_KEY or GROKTOCRAWL_API_KEY env vars ([d5080ee](https://github.com/groktopus/groktocrawl/commit/d5080ee49efba0ac8152175becf9fcacae31318b))
* remove dead try/except in _compute_domain_category ([54c6f1e](https://github.com/groktopus/groktocrawl/commit/54c6f1efc43b009a2ae005f54b5a56e008c0c755))
* remove duplicate /v1 path in FlareSolverr URL construction ([#284](https://github.com/groktopus/groktocrawl/issues/284)) ([2656b65](https://github.com/groktopus/groktocrawl/commit/2656b656b6d536475a325f3979b8818ce117ca3a)), closes [#283](https://github.com/groktopus/groktocrawl/issues/283)
* remove unused RQ queue code ([#233](https://github.com/groktopus/groktocrawl/issues/233)) ([11663bd](https://github.com/groktopus/groktocrawl/commit/11663bd79d8a20d9c90953bda3dafbfd21fab489)), closes [#196](https://github.com/groktopus/groktocrawl/issues/196)
* rename unused variable to silence vulture dead-code check ([e7253cb](https://github.com/groktopus/groktocrawl/commit/e7253cbab16ad27e185e8b3eeb5da365f007a016))
* replace debug print() with structured logger in monitor.py ([#250](https://github.com/groktopus/groktocrawl/issues/250)) ([fda98a7](https://github.com/groktopus/groktocrawl/commit/fda98a763a7f25d05105036241bfbff278fb44e0)), closes [#242](https://github.com/groktopus/groktocrawl/issues/242)
* send API key as Bearer token in CLI requests ([d0221ae](https://github.com/groktopus/groktocrawl/commit/d0221ae9c047f5b56f16d412ca6914e20c09ccd0))
* send API key as Bearer token in CLI requests ([a037388](https://github.com/groktopus/groktocrawl/commit/a0373889ee3d02cce222de598ae7cac6afd4e132)), closes [#177](https://github.com/groktopus/groktocrawl/issues/177)
* stop firing real searches in agent-svc health check ([2e18603](https://github.com/groktopus/groktocrawl/commit/2e186036602d44d1dae377a19d6243bab730fd33))
* stop firing real searches in agent-svc health check ([#217](https://github.com/groktopus/groktocrawl/issues/217)) ([46bc439](https://github.com/groktopus/groktocrawl/commit/46bc439a44ba4f4d7cfb489ce97f9d3790779d22))
* tune code-quality CI thresholds and add setuptools config ([12226c8](https://github.com/groktopus/groktocrawl/commit/12226c861d34e2bb71b30e64aeabeffd05b3ef20))
* tune code-quality CI thresholds and add setuptools config ([#269](https://github.com/groktopus/groktocrawl/issues/269)) ([6af7293](https://github.com/groktopus/groktocrawl/commit/6af72932fdd53dbd42db1522411232e37bdf958c))


### Documentation

* add Shopify adapter to README ([154fb77](https://github.com/groktopus/groktocrawl/commit/154fb7780f82ac55c84c90c890bcbc16e84e5a25))
* add Shopify to AGENTS.md adapter listing ([fa7d701](https://github.com/groktopus/groktocrawl/commit/fa7d7015a746f21fdb2e292abb9a9b904bafc50d))
* bring Firecrawl comparison table current with v0.7.0 features ([e7e0ccb](https://github.com/groktopus/groktocrawl/commit/e7e0ccbe63b50c07d0932988154d81f57fc13ddd))
* document full adapter architecture — 20 adapters across 4 categories ([44a0d5d](https://github.com/groktopus/groktocrawl/commit/44a0d5d7e8afc402a8e8289b15347fdf466644c6))
* document full adapter architecture — 20 adapters across 4 categories ([0d892e2](https://github.com/groktopus/groktocrawl/commit/0d892e20c2e167c96aa66dfa1be76bf017a5b0f4)), closes [#169](https://github.com/groktopus/groktocrawl/issues/169)
* fix Firecrawl comparison — use — for unverified, correct self-hosted status ([9797731](https://github.com/groktopus/groktocrawl/commit/97977316bd4a5ede1a9ebf27d61002826e55cffa))
* only mark self-hosted rows I actually verified — rest — ([c032c7a](https://github.com/groktopus/groktocrawl/commit/c032c7a900555401e7daf652d6be033df80ea6b9))
* promote ADR-0031 from proposed to accepted ([#201](https://github.com/groktopus/groktocrawl/issues/201)) ([e992921](https://github.com/groktopus/groktocrawl/commit/e99292187885097769b6db4c4004d48212a3c1af))
* promote ADR-0032 from proposed to accepted ([#209](https://github.com/groktopus/groktocrawl/issues/209)) ([9a61aa5](https://github.com/groktopus/groktocrawl/commit/9a61aa55cc2344f2a4fc7ffd8c70283ace5dab27))
* promote ADR-0033 to accepted ([#215](https://github.com/groktopus/groktocrawl/issues/215)) ([fde96f4](https://github.com/groktopus/groktocrawl/commit/fde96f4e9b7767474e9413c5ec80952fdfde348e))
* promote ADR-0034 from proposed to accepted ([#229](https://github.com/groktopus/groktocrawl/issues/229)) ([6f56a14](https://github.com/groktopus/groktocrawl/commit/6f56a146eab8947270f2b47b7dabab664dab75c5))
* promote ADR-0035 from proposed to accepted ([#232](https://github.com/groktopus/groktocrawl/issues/232)) ([6a83d5d](https://github.com/groktopus/groktocrawl/commit/6a83d5db039bd2823a6e069c2bd8418feffcdc2a))
* replace — with ❌ in comparison table for consistency ([ef99a8f](https://github.com/groktopus/groktocrawl/commit/ef99a8fffca81867ad1fd513d728594baa90f32d))
* simplify Self-contained Docker row to emoji-only ([0f46b0e](https://github.com/groktopus/groktocrawl/commit/0f46b0e76e36bfc0d17ab776bdce3f766388e14e))
* update AGENTS.md with current adapter categories (20 total) ([41a9407](https://github.com/groktopus/groktocrawl/commit/41a94073d542ba030ae944c4a9e81e2e84150454))
* update README and CONTRIBUTING for current architecture ([dbe5a0a](https://github.com/groktopus/groktocrawl/commit/dbe5a0a5d4c441c0da14d6c3f719863f2ee45e30))

## [Unreleased]

### Added

- **Full crawl engine with Firecrawl v2 feature parity** — replaces the stub `/v2/crawl` endpoint with a full recursive BFS crawl engine. Includes: shared `LinkExtractor` module used by crawl, map, and llmstxt; `CrawlEngine` with configurable concurrency (`maxConcurrency`, `delay`), BFS queue, `max_pages`/`max_depth` enforcement, path filtering (glob and regex with `regexOnFullUrl`), URL dedup with `ignoreQueryParameters`, `crawlEntireDomain`, `allowSubdomains`, `allowExternalLinks`; `SitemapParser` with robots.txt discovery and fallback to common locations; `DedupManager` with canonical tag and SHA-256 content hash dedup; `CrawlCache` with Valkey-backed `maxAge`/`minAge` semantics. New endpoints: `GET /v2/crawl/{id}/errors` (per-URL errors with error types, HTTP status codes, timestamps), `GET /v2/crawl/active` (crawl-specific active job listing), `POST /v2/crawl/params-preview` (NL-to-params preview). Enhanced `CrawlStatusResponse` with `next` pagination, `createdAt`, `completedAt`, `expiresAt`, `duration`, and per-page metadata (title, statusCode, content_type, scraped_at, duration_ms). SSE streaming via `stream: true` with per-page and done events. Per-page webhooks with HMAC signing. NL-to-params via `prompt` field on `CrawlRequest`. Advanced `ScrapeOptions` passthrough (actions, location, proxy, blockAds, parsers). Full test coverage in `tests/test_crawler.py` and expanded integration tests.

### Changed

- **Split `scraper-svc/scraper/fetch.py` (1751 lines → 25 lines)** — extracted 5 focused modules: `cache.py` (Valkey cache client + freshness revalidation), `proxy.py` (httpx + Playwright proxy config), `dns_guard.py` (DNS rebinding + SSRF protection), `barrier.py` (bot challenge detection), and `fetch_strategy.py` (three-tier fetch pipeline). `fetch.py` is now a thin re-export. Updated `politeness.py` and `recovery.py` imports. No behavioral changes, no new dependencies. (closes #188)

- **Consolidated urlparse imports into shared `common/url.py`** — new module with `normalize_url()`, `extract_domain()`, `is_same_origin()`, and `is_private_host()`. Refactored 17+ inline `urlparse` call sites across agent-svc, scraper-svc, and browser-svc. Pure refactor — no behavior changes. Dockerfiles updated with `COPY common/ common/`. 28 new unit tests. (closes #194)

### Added

- **Greenhouse and AshbyHQ ATS adapters** — adds two ATS (Applicant Tracking System) adapters for structured job listing extraction. Greenhouse routes through the `boards-api.greenhouse.io` REST API with readability-lxml + markdownify content conversion. AshbyHQ extracts job data from SSR-embedded `window.__appData` JSON — no API calls needed. Both follow the existing `SiteAdapter` + `@adapter` decorator pattern. See `scraper-svc/scraper/adapters/greenhouse.py` and `scraper-svc/scraper/adapters/ashbyhq.py`. (closes #206, closes #207)

- **Richer content extraction on `/v2/search` and `/v2/scrape`** — new optional `contents` parameter controls per-result content granularity. Supports verbosity levels (`compact`/`standard`/`full`), section filtering (`include`/`exclude` by category), LLM-extracted highlights and summaries, and extras extraction (links, imageLinks, codeBlocks). Backward compatible — existing behavior unchanged when `contents` is omitted. (closes #328)

- **Search volume controls for agent-svc (ADR-0033)** — two independent mechanisms to prevent runaway Brave API consumption: (1) per-request max-searches cap (`AGENT_MAX_SEARCHES_PER_REQUEST`, default 5) enforced inside `SearXNGClient` before each search call, raises `RateLimitedError` (429) when exceeded; (2) per-client sliding-window rate limit (`AGENT_SEARCH_RATE_LIMIT`, default `10/60s`) using Valkey `INCR`/`EXPIRE`. New `X-Search-Budget` and `X-Search-Rate-Remaining` response headers. Search volume observable via new `search_calls_total` metrics counter. Backward compatible — existing callers see 429s only if they exceed limits. No new dependencies. See `docs/adr/0033-search-volume-controls.md`. (closes #213)

- **Project Gutenberg adapter** — extracts books as chapter-structured markdown. Three-tier fallback chain: EPUB → plain text → generic pipeline. Zero new dependencies. Enriches metadata via Gutendex API (title, author, subjects, language). Registered at priority 200. See `scraper-svc/scraper/adapters/gutenberg.py`. (closes #181)

- **Batch vector ingestion via Qdrant gRPC (ADR-0030)** — adds `POST /index/batch` to semantic-svc for batched embedding and Qdrant upsert. Batch scrape and crawl workers now accumulate pages and fire a single batch call instead of N per-page calls. Expected: 500-page crawl indexing drops from ~50s to ~250ms (200x). New tests: `test_batch_index_endpoint`, `test_batch_index_empty`. Legacy flat-vector Qdrant collections auto-migrate to named vectors on startup. See `docs/adr/0030-batch-vector-ingestion.md`. (closes #154)

- **Service-level metrics for semantic-svc (ADR-0029)** — adds Prometheus-compatible `/metrics` endpoint to semantic-svc with stdlib-based OpenMetrics format (no new dependencies). Metrics tracked: document count gauge (`groktocrawl_index_docs_total`), eviction counter (`groktocrawl_index_evictions_total`), request latency histogram per endpoint (`groktocrawl_index_query_duration_seconds`), embedding inference duration (`groktocrawl_index_embeddings_duration_seconds`), and request counter per endpoint (`groktocrawl_search_requests_total`). ASGI middleware instruments all 11 existing endpoints automatically. Eviction counter tracks cumulative evictions via `_evict_if_needed()`. See `docs/adr/0029-service-level-metrics-for-semantic-svc.md`. (closes #153)

- **Embedding model migration path — named vectors, backfill, dual-write, cutover (ADR-0028)** — supports upgrading the embedding model without dropping the index. Uses Qdrant named vectors for per-point multi-model storage. New endpoints: `GET /index/model` (model config + migration state), `POST /index/migrate/start` (begin backfill), `GET /index/migrate/status` (progress), `POST /index/migrate/cutover` (switch queries). New payload fields: `embedding_model`, `embedding_dim`, `embedding_models` (history). Dual-write mode indexes with both old and new model during migration. Old vectors retained after cutover for rollback safety. See `docs/adr/0028-embedding-model-migration-path.md`. (closes #155)

- **Unit test coverage for worker.py and research.py** — adds `test_worker.py` (19 tests, 693 lines) covering all 7 worker processing functions, and 23 new tests for previously uncovered research.py functions (`run_extract`, `run_research_stream`, `run_answer_stream`, `run_rich_search`, `_rerank_answer_sources`). Agent-svc/agent coverage from ~15% to 55%. Coverage threshold raised to 20%. (closes #192)

### Fixed

- **Playwright health probe and Tier-3 integration tests** — adds `test_scraper_health_reports_playwright` and `test_scraper_falls_through_to_playwright` to catch missing `playwright install-deps chromium` (fixes #258). Adds `tier3-fixture` service with JS-rendered dynamic content to force Playwright fallback. Scraper `/health` now returns `checks.playwright.available`. (closes #260)

- **Default URL scheme typos in settings** — all four agent-svc default URLs (`scraper_url`, `searxng_url`, `semantic_url`, `llm_base_url`) had `http//` instead of `http://`, causing agent to fail when `.env` didn't explicitly set these. (closes #260)

- **CI workflow fixture setup** — `llm-svc` was not started in the "Start test fixtures" step, breaking the 2 streaming endpoint tests. `LLM_BASE_URL` and `LLM_MODEL` now written to `.env` before stack startup so the agent daemon uses the fixture LLM. Semantic-svc health check now validates Qdrant reachability from inside the agent-svc container. (closes #260)

- **Flaky activity feed tests** — `test_activity_shows_active_crawl_job` and `test_activity_multi_type` failed when fast single-page crawls completed before the activity feed check. Both now fall back to the job status endpoint when the job is no longer in the active feed. (closes #260)

---

## [0.7.0] — 2026-06-09

### ⚠️ Breaking Changes

- **Two new required services** — `semantic-svc` and `qdrant` are now part of the docker-compose stack. `docker compose up` will fail if these images cannot be pulled. First-time startup requires pulling the Qdrant image, and the semantic-svc needs to create the initial collection — expect a longer first spin-up.
- **New optional service** — `portal-svc` (port 8082:8081) added to docker-compose. Not required for API operation but published by default.
- **Scraper-svc now loads `.env`** — the scraper container has `env_file: .env` in docker-compose.yml. Existing deployments with custom `.env` files will see new env vars picked up automatically.
- **`search_type` defaults to `fast` on `/v2/search`** — this matches existing behavior but is now an explicit parameter. The new `rich` mode is opt-in.

### 🧠 Semantic Search Engine (Phase 1 & 2)

GroktoCrawl evolves from a stateless scraper into a learning search engine with a persistent vector index and ad-hoc semantic reranking.

- **Phase 1: Semantic reranking (#142)** — `semantic-svc` performs embedding-based cosine reranking of SearXNG results on every `/v2/search` call. Uses pure-Python dot product (no numpy dependency, #143). Wired through the `/v2/answer` endpoint via `retrieval_mode` parameter (#156).
- **Phase 2: Persistent vector index with Qdrant (#145)** — every page GroktoCrawl scrapes, crawls, or maps is embedded and stored in a Qdrant vector index (up to 250K docs by default). Subsequent searches query both SearXNG and the local corpus — the project's accumulated knowledge becomes searchable. Qdrant v1.18.2 pinned for reproducibility (#158).
- **Batch vector ingestion via Qdrant gRPC (#163)** — large crawl results are ingested in batches for efficiency. New `semantic-svc/semantic/index.py` module.
- **Content-based near-duplicate detection (#157)** — configurable cosine threshold (default 0.95) identifies duplicates at indexing time. `NEAR_DUP_THRESHOLD` and `NEAR_DUP_MODE` (skip/update) env vars.
- **Smarter index retention — domain TTLs, frequency weighting, access boosting (#159, ADR-0027)** — replaces simple LRU eviction with a scoring function that considers content type, crawl frequency, and access patterns. News/social evicts first; reference/docs content persists longest.
- **Embedding model migration path (#160)** — named vectors support enables future embedding model upgrades with backfill, dual-write during migration, and clean cutover.
- **Service-level metrics for semantic-svc (#161, ADR-0029)** — stdlib-based OpenMetrics: `groktocrawl_index_docs_total`, `groktocrawl_index_evictions_total`, `groktocrawl_index_query_duration_seconds`, `groktocrawl_index_embeddings_duration_seconds`.
- **Thread executor fix (#166)** — `model.encode()` now runs in a thread executor to avoid blocking the async event loop, fixing concurrent request handling under load.
- **Qdrant API compatibility fixes (#146, #165)** — corrects point ID type (uint64 via SHA-256 truncation), uses `using` instead of `query_vector_name` for qdrant-client 1.18 compatibility.

### 🌐 Web Portal (portal-svc)

A human-facing web interface for GroktoCrawl, available at port 8082.

- **Single-search-bar UI (#132, ADR-0021)** — FastAPI + Jinja2 interface with Google-inspired design. Routes queries to `/v2/answer` with SSE streaming, real-time token output with citations, and a recent queries sidebar (localStorage-persisted).
- **Client-side markdown rendering** — uses the `marked` library for full GFM rendering of answer text (tables, bold, inline code, links).
- **Answer-above-sources layout** — primary content displayed first, with scrollable source citations below.
- **Placeholder deep research button** — reserved for future v0.2 integration with agent-SSE.

### 🎤 Grounded Q&A (/v2/answer)

A new synchronous single-turn Q&A interface sits between `/v2/search` (raw results) and `/v2/agent` (deep multi-step research), giving users a fast grounded-answer path.

- **POST /v2/answer endpoint (#117, ADR-0017)** — pipeline: search → scrape → LLM synthesis → citations in one round-trip. Returns structured response with `answer` (markdown), `sources` (list), `citations` (index↔URL mapping), `search_type`, and `latency_ms`.
- **SSE streaming** — `stream: true` enables token-by-token streaming with source discovery events before LLM output. Reuses protocol from ADR-0017.
- **Concurrent scraping with early sources emission (#135)** — `_scrape_urls()` now scrapes in parallel with semaphore control, emitting sources as they complete instead of waiting for all.
- **Retry on scrape failure (#120)** — loops through search results until enough sources succeed (bounded by 2x requested count). Prevents empty responses when top results are from hostile sites but usable sources exist deeper.
- **Video platform URL deprioritization (#138)** — YouTube, Vimeo, and Twitch URLs are scored lower in source selection, preferring text-based sources.
- **`answer` CLI subcommand (#118)** — `groktocrawl answer "question"` with `--sources`, `--model`, `--num-sources`, and streaming defaults.

### 🔌 New Site Adapters

Building on the adapter framework introduced in v0.6.0, three new adapters add structured extraction for high-value content sources.

- **GitHub adapter (#115)** — `scraper-svc/scraper/adapters/github.py` for raw file content, blob URLs, repo roots (README + metadata), and tree listings. Priority 200.
- **GitHub social adapter (#115)** — `scraper-svc/scraper/adapters/github_social.py` for issues, PRs, discussions, releases, and commits via GraphQL API (v4). Three-tier fallback: GraphQL → REST → HTML scrape. Priority 190.
  - `GITHUB_TOKEN` env var enables 5,000 req/hr + GraphQL.
  - Works without a token at 60 req/hr (REST) or unauth (HTML scrape).
- **Substack adapter (#122)** — three-tier extraction via RSS feed (primary, no auth) → readability-lxml → browser-svc. Detects both `*.substack.com` and vanity domains via RSS fingerprinting. 26 unit tests.
- **Reddit adapter (#121)** — post and comment extraction via the JSON API (`.json` suffix).

### 🔍 Search Improvements

- **Search type spectrum — fast and rich (#140, ADR-0023)** — `/v2/search` accepts `search_type` (default: `fast`). `fast` is instant (existing behavior). `rich` mode scrapes top results and enriches with LLM synthesis (1-3s). Optional `output_schema` enables structured data extraction in a single call.
- **Agent SSE streaming (#137, ADR-0022)** — `/v2/agent` now supports `stream: true` for Server-Sent Events. Two-phase streaming: discovery events followed by token-by-token LLM output. CLI defaults to streaming; `--sync` to opt out.
- **Vertical search categories (#101, #102)** — `sources` and `categories` parameters on `/v2/search` translated to SearXNG-native categories.

### 🛡️ Politeness Protocol & Proxy Support

- **Politeness protocol (#114)** — new `scraper-svc/scraper/politeness.py` module. Gated behind `SCRAPER_POLITENESS_ENABLED=true`. Fetches and caches robots.txt, enforces Crawl-delay, blocks Disallow paths. 14 unit tests.
- **Proxy support — SCRAPER_PROXY_URL (#129, ADR-0020)** — contributed by @Jackal991. Single env var plumbed through httpx clients and Playwright browser context. Fail-open with WARN on proxy failure.

### 📊 Observability & Operations

- **Health probes and /metrics endpoint (#123, ADR-0018)** — `/health` returns per-dependency probe results. `/metrics` exports counters, histograms, and gauges in OpenMetrics format. Structured JSON logging with request_id correlation.
- **Health endpoint fix (#125)** — corrected `health` → `healthy` state attribute name.

### 🧹 Quality & Cache

- **Intelligent scrape cache — ETag/Last-Modified revalidation (#126, ADR-0019)** — the Valkey-backed cache now uses conditional GETs. 304 responses extend TTL without re-downloading. SHA-256 content hashing detects changes. Per-domain TTLs, configurable bounds, volatility-aware TTL adjustment.
- **Extraction QA pipeline (#111, ADR-0016)** — post-extraction quality gates for boilerplate detection, completeness checks, and block page detection.
- **Graceful degradation (#112)** — `smart_scrape()` degrades through tiers on low-quality content.
- **Structured metadata enrichment (#116, ADR-0004)** — extracts JSON-LD, OpenGraph, Twitter Card, and meta tags alongside markdown.
- **Artifact-pyramid CLI output (#141, ADR-0024)** — `--pyramid` flag on `agent` and `answer` commands.

### 🔧 Fixes & Polish

- **Async event loop unblocked (#166)** — synchronous `model.encode()` now runs via thread executor.
- **Qdrant API compatibility (#146, #165)** — uint64 point IDs, `using` keyword for qdrant-client 1.18.
- **Numpy dependency removed (#143)** — pure-Python dot product replaces numpy in semantic reranking.
- **Port exposure cleanup** — semantic-svc port no longer exposed to host.
- **Conditional auth header** — `Authorization: Bearer` only sent when `api_key` is non-empty.
- **ADR cleanup** — stale ADR-0020 removed, proxy ADR renumbered.

### Contributors

Special thanks to everyone who contributed to this release:

- **@Jackal991** — PR #129: `SCRAPER_PROXY_URL` env var with fail-open logic, proxy identity logging, and credential redaction. A clean, minimal implementation that fills a structural gap in the scrape pipeline.
- **@wysie** — PR #87: uv setup documentation for CLI dependencies. PR #86: portable Docker Compose config and SearXNG JSON API enabling.

And @magnus919 for the bulk of the work — 52 PRs across semantic search, web portal, answer endpoint, adapters, observability, caching, and quality infrastructure.

### Infrastructure

- **New Docker services**: `semantic-svc`, `qdrant`, `portal-svc`
- **New Qdrant volume**: `qdrant_data` for persistent vector storage
- **New environment variables**: `SCRAPER_PROXY_URL`, `GITHUB_TOKEN`, `SCRAPE_CACHE_DOMAIN_TTLS`, `SCRAPE_CACHE_MIN_TTL`, `SCRAPE_CACHE_MAX_TTL`, `SCRAPE_CACHE_VOLATILE_CAP`, `SCRAPE_CACHE_STABLE_MULTIPLIER`, `SCRAPER_POLITENESS_ENABLED`, `SCRAPER_POLITENESS_CRAWL_DELAY`, `SCRAPER_POLITENESS_ROBOTS_TTL`, `QA_MIN_QUALITY_THRESHOLD`, `NEAR_DUP_THRESHOLD`, `NEAR_DUP_MODE`, `SEMANTIC_URL`, `QDRANT_URL`, `VECTOR_INDEX_MAX_DOCS`
- **First-time startup note**: `docker compose up` will pull the Qdrant v1.18.2 image (~50MB). The Qdrant collection is created automatically on first index. Expect ~2-3 minutes for initial service readiness.

### Full PR List

PRs #83, #86–87, #89–90, #94, #96–97, #101–105, #111–118, #120–123, #125–126, #128–129, #132, #135–138, #140–143, #145–149, #156–168.

---

## [0.6.0] — 2026-06-05

### Added

- **Adapter framework** — pluggable site-specific content handlers with auto-registration, priority-sorted dispatch, and per-adapter fallback chains. See `docs/adr/0001`–`0009`.
- **YouTube adapter** — extracts full video transcripts and descriptions via `youtube_transcript_api` (free, no key). Returns YAML frontmatter (title, channel, views) + description + transcript as markdown.
- **Bluesky adapter** — extracts posts and threads via the AT Protocol public API (no auth required). Returns YAML frontmatter (author, handle, engagement stats) + post text with richtext facet conversion (mentions, links, tags) + depth-1 replies as markdown.
- **Barrier classification (Phase 1)** — `_classify_barrier()` replaces the boolean `_looks_suspicious()` heuristic. Detects Cloudflare, DDoS-Guard, CAPTCHA, rate-limit, Substack redirect, and empty-content barriers with confidence scoring. ADR-0015.
- **Valkey scrape result cache** — TTL-based cache (default: 1 hour) for scrape results. Configurable via `SCRAPE_CACHE_TTL` env var. Adapter results excluded from cache.
- **Search failure detection** — `SearchHealth` dataclass reports per-query engine status (total engines, responding engines, degraded vs empty-result signal).
- **Firecrawl v2 category translation** — `sources` and `categories` parameters on `/v2/search` are translated to SearXNG-native categories. `sources=news` → `categories=news`, `categories=research` → `categories=science`, etc. CLI exposes `--sources` and `--categories` flags.
- **Architecture-as-code** — C4 system-context and container diagrams in `docs/architecture.md`. GitHub Actions CI workflow validates ADR naming, required sections, and index freshness.
- **Architecture Decision Records (ADRs 0001–0015)** — covers adapter framework, scraper pipeline, stealth Playwright, webhooks, search architecture, binary content, barrier classification.

### Changed

- `smart_scrape()` now: (1) checks adapter registry, (2) checks Valkey cache, (3) runs barrier classification after each tier, (4) runs the existing 5-tier pipeline.
- `/v2/search` response routes results to the correct top-level key (`data.web`, `data.news`, `data.images`) based on the `sources` filter.
- `SearchRequest` model now accepts `sources: list[str] | None`.
- CLI search subcommand now accepts `--sources` (web, news, images, video, social) and `--categories` (research, github, pdf, etc.).

### Documentation

- Architecture Decision Records: 15 total (was 9).
- `docs/architecture.md` — C4 System Context and Container diagrams.
- `CONTRIBUTING.md` — ADR convention section.
- `AGENTS.md` — search parameters documentation for AI agents.
- `README.md` — Search endpoint docs with parameter and translation tables, detailed Adapters section (YouTube + Bluesky).
- `.env.sample` — added `SCRAPE_CACHE_TTL`, `ADAPTER_YOUTUBE_API_KEY`.

### Infrastructure

- `.github/workflows/architecture.yml` — CI pipeline validating ADR structure on push/PR to main.

## [0.5.0] — 2026-05-31

### Security

- **API key authentication** — Set `API_KEY` in `.env` to enable bearer token auth. All endpoints (except `/health`) require `Authorization: Bearer` or `X-API-Key`. When unset, a startup warning is logged, an `X-Security-Warning` header is added to every response, the `/health` endpoint includes a structured `security` field, and the CLI prints a one-time stderr warning. Backward compatible — existing deployments work unchanged. (#83)
- **Private IP / SSRF protection** — Both `browser-svc` and `scraper-svc` now validate destination URLs before navigation. RFC 1918 private ranges, loopback, link-local, cloud metadata endpoints (169.254.169.254), and Docker host suffixes (`.docker.internal`) are blocked with a 400 error. Hostnames are resolved to IPs and checked, preventing DNS rebinding attacks. (#83)
- **Port hardening** — Removed host port exposure from `browser-svc` (8012), `scraper-svc` (8001), and `parse-svc` (8013). These services are only reachable on Docker's internal DNS. The agent API on port 8080 remains the sole external entry point. (#83)

### Changed

- **Breaking**: `browser-svc`, `scraper-svc`, and `parse-svc` no longer publish host ports. Scripts or tools that connect directly to these services on ports 8012, 8001, or 8013 must be updated to go through the agent API on port 8080. (#83)
- **Breaking**: Existing `.env` files that manually set `SCRAPER_URL=http://localhost:8001` will break. Change to `http://scraper-svc:8001` (Docker internal DNS). (#83)
- `docker-compose.yml` restructured — internal services no longer expose ports. (#83)

### Added

- New `agent-svc/agent/auth.py` — centralized authentication module with `verify_api_key()` FastAPI dependency. (#83)
- `SECURITY.md` — security policy, supported versions, and disclosure acknowledgments. (#83)

### Credits

This release was prompted by a responsible disclosure from **Bertie**, who
privately reported the unauthenticated browser pivot vulnerability. Thank you.

## [0.4.0] — 2026-05-31

### Added

- **CLI subcommands for monitor, parse, and generate-llmstxt** (`groktocrawl` binary) — three new entry points for managing change monitors (create/list/get/update/delete), parsing document files (PDF, EPUB, DOCX) to markdown, and generating llms.txt for a website with async polling. (#79, #81)
- **Agent system prompt upgrade** (`agent-svc/agent/research.py`) — replaced the minimal 7-line `SYSTEM_PROMPT` with a comprehensive prompt that instructs the LLM to evaluate source quality, synthesize across multiple pages, detect contradictions, flag thin evidence, and cite sources by URL. The new prompt defines a clear source authority ladder (official docs > established news > blogs/forums) and tells the agent to be thorough and precise rather than just "concise."
- **Extract prompt upgrade** — `EXTRACT_SYSTEM_PROMPT` now instructs the LLM to extract ALL instances of requested data, flag missing/ambiguous values, and organize output clearly.
- **Model selection passthrough** — the `model` field from `POST /v2/agent` requests is now respected. When set to a specific model name (e.g., `"gpt-4o"`) it overrides the environment-configured default.
- **Domain metadata in context** — each scraped source now includes `(domain: example.com)` in the context passed to the LLM.

### Changed

- **Search results format** — `/v2/search` now returns results grouped by source type per Firecrawl v2 spec (`{"data": {"web": [...]}}`) instead of a flat array. (#66)

### Fixed

- **50x search speedup** — removed redundant per-result scraping from `/v2/search`. (#69)
- **Search CLI parsing** — `groktocrawl search` correctly reads from `data.web` dict instead of the flat `data` list. (#71)

## [0.3.0] — 2026-05-24

### Added

#### Substack Scraping (Stealth Playwright Config)

- **Stealth Playwright renderer** (`scraper-svc/scraper/stealth.py`) — the scraper-svc's Tier 3 now launches Chromium with `--disable-blink-features=AutomationControlled`, a real Chrome 131 User-Agent, 1920x1080 viewport, `en-US` locale, `America/New_York` timezone, and `navigator.webdriver` override via `add_init_script()`.
- **SPA content retry** — when extracted markdown is short (< 500 chars) or suspicious, the scraper scrolls to the bottom of the page and waits up to 6s to trigger lazy-loaded content.
- **Substack redirect detection** — `_is_substack_redirect()` detects `session-attribution-frame`, `channel-frame`, and GTM noscript redirects.
- **Content gate fix** — `smart_scrape()` now returns extracted content immediately when `_looks_suspicious()` passes, even if embedded content signals are present.

#### Cookie Persistence (scraper-svc)

- **Valkey-backed Cloudflare cookie store** — `cf_clearance` cookies are cached and reused across scrapes via Valkey (25-minute TTL, TLD+1 domain scoping).
- **Cookie injection before navigation** — `fetch_via_playwright()` injects stored `cf_clearance` cookies before navigating.

### Changed

- **Browser args stripped** — removed `--disable-web-security`, `--disable-features=IsolateOrigins,site-per-process`, and `--disable-features=BlockInsecurePrivateNetworkRequests` from the stealth config.

### Fixed

- **False-positive embedded content detection** — `substackcdn.com` was falsely matching the `cdn.` domain pattern. Fixed by prioritizing `content_good` over embedded content signals.

## [0.2.0] — 2026-05-24

### Added

#### Five-Tier Scrape Pipeline

- **Tier 3.5: FlareSolverr** — optional profile-gated container for hard Cloudflare challenges.
- **Tier 4: LLM-Assisted Recovery** — when standard tiers return suspicious content, the scraper calls a configured LLM to analyze the page.
- **Tier 5: LLM Cloudflare Classification** — when all bypass methods fail, the LLM explains the block type and suggests alternative access paths.

#### Binary Content Support

- **Content-Type detection** — auto-detects PDF, EPUB, images, and archives at the HTTP tier. Returns a structured `download` payload.
- **`groktocrawl download <url>`** — new CLI subcommand for binary content.

### Infrastructure

- **Contribution templates**: added bug report and feature request issue templates, PR template, updated `CONTRIBUTING.md`.

## [0.1.1] — 2026-05-24

### Added

- **Unified activity endpoint** (`GET /v2/activity`) — lists all active jobs across all job types.
- **Lightweight meta tag extraction** (`POST /scrape/meta`) — single HTTP GET to extract `<title>`, `<meta name="description">`, and `<meta property="og:description">`.
- **Sentence-boundary-aware description extraction** — llms.txt generator produces descriptions ending at complete sentence boundaries.

### Fixed

- CLI `active` command (no longer returns 404).
- llms.txt descriptions no longer truncated mid-sentence or include boilerplate.

## [0.1.0] — 2026-05-21

Initial release. Self-hosted, Firecrawl-compatible web scraping and AI research API.

- `/v2/scrape`, `/v2/crawl`, `/v2/batch/scrape`, `/v2/search`, `/v2/map`, `/v2/agent`, `/v2/extract`, `/v2/browser`, `/v2/monitor`, `/v2/parse`, `/v2/generate-llmstxt`
- CLI with all endpoint support
- Valkey-backed job store with 24h TTL
- Webhook delivery with HMAC signing and retry
- Docker Compose deployment
