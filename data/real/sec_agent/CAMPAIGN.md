# SEC Form 4 agent-lane campaign — final selection (2026-09-20)

Seed rule (owner-approved scope): for each of the ten tracked tickers, the up-to-eight
most recent Form 4 filings **filed on or after 2026-06-01**, captured as the SEC's own
XSL-rendered views plus filing indexes through the page-fetch route. Enumeration route:
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&type=4&owner=include&count=40&output=atom`
(note: browse-edgar treats `type=4` as a prefix filter, so high-volume filers like JPM
also return 424B2 entries that were discarded) and, earlier in the campaign,
`https://efts.sec.gov/Latest/search-index?q=%22%22&forms=4&dateRange=custom&startdt=2026-06-01&enddt=2026-09-20&ciks=<cik>`
(now answering Forbidden to this route; the atom route remains).

Every file below is SHA-256 digested in `staged_manifest.json` and re-verified by
`scripts/stage_sec_rendered.py` at staging time; the parsed rows carry the SEC page URL,
the digest and `channel: agent-rendered-extract`.

## AAPL (issuer CIK 0000320193) — 9 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0001140361-26-025620 | 52194739c5271fb7 | b989bd144df7f0ad | captured |
| 0001140361-26-025622 | de42ca63cc249d94 | 9c3c6f6d7b0d0e61 | captured |
| 0001140361-26-032884 | 7886cfe750653a7e | 7b0262aea56d94a6 | captured |
| 0001140361-26-033928 | 80d856392433b482 | 0b7e0750d795a8bc | captured |
| 0001140361-26-034741 | c1ff18ed2dbd8c00 | 3c6f8f825641db0c | captured |
| 0001140361-26-035362 | ad82cb670aa58070 | 4e3446f963f7b0d4 | captured |
| 0001140361-26-035636 | 27a2193871c9f319 | d276c002a9f2a759 | captured |
| 0001140361-26-036226 | 0a28c561518ae206 | a49d439f4dc3c712 | captured |
| 0001140361-26-037020 | a1bb48315bee25ac | f37994ae54d90b66 | captured |

## MSFT (issuer CIK 0000789019) — 8 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0000789019-26-000212 | a7201dcf8d6713b1 | 13a568c3dfcb23ca | captured |
| 0000789019-26-000213 | f3aaab7ba0599b6e | b03f2061bcd0e523 | captured |
| 0000789019-26-000214 | 71fc3c6b9ff15fc3 | ad50fd8729ba1151 | captured |
| 0000789019-26-000220 | d3a3a11bfe6a2616 | a784343a247dbf14 | captured |
| 0000789019-26-000221 | 5349cce82d2af19b | eb81591a670be9e6 | captured |
| 0000789019-26-000222 | 348d5f5863e25cec | 30dd42f3c2776086 | captured |
| 0000789019-26-000223 | 82526591310cfc9d | 624e69a6fc67ace5 | captured |
| 0000789019-26-000224 | 14e95374b1ea7a64 | 9a8589798799bee6 | captured |

## NVDA (issuer CIK 0001045810) — 8 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0001197647-26-000009 | bd0953238aa543f3 | bc8155b4718d4db9 | captured |
| 0001197649-26-000012 | 00a091bfab2a31f2 | d403214c72165398 | captured |
| 0001199039-26-000014 | f7e8ca973c74fd86 | 46d81ec98bc56be2 | captured |
| 0001243821-26-000007 | 8bbb0a88f805c5b3 | 0492c1a6676849a5 | captured |
| 0001283854-26-000010 | 0ac0b182b7b262a1 | b5ea35545a5fc88a | captured |
| 0001588670-26-000014 | 3d8e267734c71517 | 580292cd2e51fa37 | captured |
| 0001696841-26-000012 | 29d4e13c4d67fda1 | ff1e518197145768 | captured |
| 0002152188-26-000005 | 36a26fd292f79464 | ac18609c62e1e69e | captured |

## JPM (issuer CIK 0000019617) — 8 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0001225208-26-006275 | 7670de560d43b1d6 | 3b31527f2fb2c0c9 | captured |
| 0001225208-26-006277 | 51d7d62baa1b7217 | d17c0626d3b2d834 | captured |
| 0001225208-26-006278 | 3f32f6ae3bf23dc9 | ca97a03965d8cb86 | captured |
| 0001225208-26-006542 | 155e191c0e6fdb75 | d08fe7afcb6a7cb5 | captured |
| 0001225208-26-006749 | 8eeb3019352fc619 | 44f89f4890a680da | captured |
| 0001225208-26-006750 | 556514d23138c109 | 0956312e6e5c5320 | captured |
| 0001225208-26-007064 | 0b617b2e3f51932c | 6d42fa88684d13b7 | captured |
| 0001225208-26-007727 | efd31d2b45d521f9 | c79dad8fdaddd4a9 | captured |

## XOM (issuer CIK 0000034088) — 2 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0000034088-26-000083 | 251d19b8ba18019a | f35ef382d7a6a222 | captured |
| 0000034088-26-000085 | 4f66dfa1007d8579 | bad6b4aec032902f | captured |

## JNJ (issuer CIK 0000200406) — 8 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0000200406-26-000169 | 7888b80968247795 | d0ef20b0f8b07e08 | captured |
| 0000200406-26-000171 | b1d2a84f687f2e47 | c586c9c9fc318f34 | captured |
| 0000200406-26-000173 | b0ac062117090936 | d855c621ee2359ad | captured |
| 0000200406-26-000175 | 343af71108e0985c | 3871211ef0a908ce | captured |
| 0000200406-26-000179 | 55a9f17947b5bcbd | 01ce5e6353dcab80 | captured |
| 0000200406-26-000183 | 3c2a47318009b539 | c614d22329b0be49 | captured |
| 0000200406-26-000184 | af12a1e0472acb8b | c70156f3440b3518 | captured |
| 0000200406-26-000185 | b3fe260ed9d1ee5c | 9cef99277bbf0171 | captured |

## PG (issuer CIK 0000080424) — 8 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0000080424-26-000136 | 0e3bc4b4ab1c04df | 094faa2cb4fdf29c | captured |
| 0000080424-26-000138 | 49c9f87556df72f5 | 4ce08f97a1ceeae8 | captured |
| 0000080424-26-000140 | 3479378b8d6328c9 | 3736cc876fc4bb73 | captured |
| 0000080424-26-000141 | 5dd39eb99d0a5446 | 3d553cc0440b96f6 | captured |
| 0000080424-26-000142 | 0bb8c8aa112060af | 5c662339f75e55af | captured |
| 0000080424-26-000148 | 80cd76941acba844 | a22eebad1a92bc12 | captured |
| 0000080424-26-000150 | 50c244cb64784070 | 4ef0b2fa270364c4 | captured |
| 0000080424-26-000162 | 225d95e72ed36e7a | d945dc1840858355 | captured |

## TSLA (issuer CIK 0001318605) — 3 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0001104659-26-071970 | e968a41cd0a8563f | 973d9d0e07c3ec24 | captured |
| 0001104659-26-075213 | bffdcccf982bb631 | e952880dc4fd3c4a | captured |
| 0001104659-26-106432 | 7c2c30e9ca18d4f3 | 407d8087a73a69e7 | captured |

## MU (issuer CIK 0000723125) — 6 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0001218363-26-000003 | 60f1b9138f17c56e | ec018862c4f11fb3 | captured |
| 0001242654-26-000012 | 9ff2f525dda1bc49 | — | (not captured — see exclusions) |
| 0001242654-26-000014 | c9e24a6a5e6f9dd6 | 5b07f2f0474e385e | captured |
| 0001453368-26-000001 | 0e70dd42206df97e | a85c5aeec9bb1396 | captured |
| 0001652149-26-000004 | ea58c958c3a206a5 | 9f6c89e06d0ce4dd | captured |
| 0001652149-26-000005 | f93e80b81cb9891e | ce2fd3c08ac287b0 | captured |

## T (issuer CIK 0000732717) — 6 filings

| accession | filed | index digest (sha256[:16]) | form |
| --- | --- | --- | --- |
| 0000732717-26-000288 | 68d882960bc8b65c | 7ed88280e01c249c | captured |
| 0000732717-26-000314 | fd2e550e7065fbaa | be178e9399c18d16 | captured |
| 0000732717-26-000315 | 586f2bf99b1ae956 | 18addebadf259453 | captured |
| 0000732717-26-000319 | d38e69c3b52e1c07 | 0ace028362276280 | captured |
| 0000732717-26-000332 | 444ebc7b19cc8f34 | f586f4cf14fdab2a | captured |
| 0000732717-26-000333 | 29bfbe8b88848e5a | a9cf60d04bbc7262 | captured |

## Exclusions (recorded, not hidden)

- MU 0000034088-26-000003 (Arntzen; form ~39 KB rendered) and MU
  0001242654-26-000013 (Mehrotra, first of two paired Forms 4; ~63 KB rendered)
  exceed the practical page-fetch size budget and were dropped. The Mehrotra
  second filing (0001242654-26-000014, 12 S rows under a 2026-01-30 10b5-1 plan,
  President and CEO) was captured instead.
- MU 0001242654-26-000012 (Mehrotra; ~42 KB rendered) likewise not captured.
- Every other Form 4 filed in window for the ten tickers was captured up to the
  eight-most-recent cap per ticker.

## Staging result

- Filings staged: **65**, digests verified: **65**,
  transactions parsed: **99**, flags: **11** (all derivative-only or
  balance-only statements, listed in `parse_report.json`).
- The window contains zero code-P open-market purchases, so the buy-side insider
  signals register NO-OBSERVATIONS (see LIMITATIONS L-27/L-35).
