# W1 comparison launch record — 2026-09-10

Status: **qualification failure; no scored result**

The first authorized launch reached no model inference. All Arm A connection
attempts failed because the configured `inference.example.internal` hostname did not expose the
LiteLLM port; the gateway is published on its LAN address. Arm B rejected its
private source aliases before transport because they were not valid HTTP URLs.

The private runner retained every attempted outcome. There are zero completed
answers and zero received model envelopes, so this launch is invalid for scoring
and supplies no evidence about either arm. The private launch manifest has digest
`sha256:16817fe7c9cec787f097210be067aee4bc2987237aaa39db90f57edf8151247e`.

The runner now checks the authenticated model listing before creating an output
directory and uses non-routable, contract-valid HTTPS identities for private
sources. A clean rerun requires an explicit amendment because the no-retry rule
was correctly conservative even though inference did not occur.

Magnus authorized one clean rerun after reviewing this disposition. The private
rerun authorization is bound to the invalid launch, original comparison decision,
call-budget correction, and runner fix; its digest is
`sha256:e1867609a62b8ee607fceafcb58f113db4e31b9f63181292109dd1dca5d0d31d`.
